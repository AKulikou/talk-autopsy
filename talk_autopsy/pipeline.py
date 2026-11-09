"""
The pipeline: one video through the selected steps, each reading and writing
its own file in `work/<video-id>/`. The default path (decided at the stage 1
gate and in B5, 2026-11-09):

    source -> audio.wav (16 kHz mono) + audio.flac (the copy that is uploaded) + meta.json
      stt        -> raw.json        AssemblyAI, first pass, no key terms
      glossary   -> glossary.auto.md (Haiku, from raw.json + --context) and
                    glossary.md      (merged with the user's --glossary, which wins)
      stt2       -> raw2.json       AssemblyAI again, the glossary as key terms
      align      -> aligned.json    forced alignment, only for model-estimated times (fallback)
      fix        -> fixed.json      Claude correction (correction-v2)
      translate  -> <lang>.json     Claude translation (translation-v2)
      srt        -> <video-id>.<lang>.srt/.ass and <video-id>.en.srt/.ass

- **Engines and fallbacks.** `engine="assemblyai"` (default): if the first
  pass fails (service error, network), the run goes on with chunked Gemini
  (`gemini+align`, free tier; per chunk it falls back to faster-whisper) when
  GEMINI_API_KEY is set, else with faster-whisper; the fallback and its
  reason are in `raw.json` meta and in a progress event. After a fallback
  the stt2 step passes the first transcript through (the key-term pass is an
  AssemblyAI feature), and so does a failed stt2. Other engines
  (`faster-whisper`, `gemini`, `gemini+align`) run as chosen; stt2 passes
  through for them. A missing ASSEMBLYAI_API_KEY is a configuration error
  before any work, not a reason to fall back.
- **Upload.** Only the compressed audio (`audio.flac`) goes to AssemblyAI,
  never the video.
- **Skip when unchanged.** Every output has a stamp file `<output>.stamp.json`
  with the hashes of its inputs and the settings that shape it. A step whose
  output exists with a matching stamp is skipped; anything else reruns it.
- **Fail fast.** Keys a selected step needs are checked before any work.
- **Cost.** Before the first Claude step the estimate of all Claude steps is
  emitted; above `max_cost_usd` the run stops before spending. `RunReport` has
  the Claude cost and the speech recognition cost of every step.
- **Refusal fallback.** On by default for `run` (`refusal_fallback`): a
  correction or translation block that the model's classifiers decline is
  answered by the fallback model, recorded in `meta.<stage>.served_by`.
  Experiments (`fix`, `translate`, `eval`) keep it off, so a benchmark row
  describes one model.
- **Progress:** `ProgressEvent`s, as in stt/chunked.py, for every step.

No CLI input or output here, so a web UI can call `run` the same way.
"""

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from talk_autopsy import align as align_module
from talk_autopsy import blocks, config, correct, glossary_auto, ingest, subtitles, translate
from talk_autopsy.glossary import Glossary, load_glossary, to_markdown
from talk_autopsy.segments import Transcript, load_transcript, save_transcript
from talk_autopsy.stt.chunked import ProgressEvent, file_sha256

logger = logging.getLogger(__name__)

STEPS = ("stt", "glossary", "stt2", "align", "fix", "translate", "srt")
CLAUDE_STEPS = {"glossary", "fix", "translate"}
ENGINES = ("assemblyai", "faster-whisper", "gemini", "gemini+align")
DEFAULT_ENGINE = "assemblyai"
NL = chr(10)


class PipelineError(RuntimeError):
    """A run cannot go on: a missing input, a cost above the limit, a failed step."""


@dataclass
class RunSettings:
    source: Path
    target_lang: str
    steps: list[str]
    engine: str = DEFAULT_ENGINE
    glossary: Path | None = None  # the user's glossary; wins over the auto-glossary on conflicts
    context: str = ""  # video title, description, chapter titles; a local file's name is not context
    work_root: Path = config.WORK_DIR
    max_cost_usd: float = config.COST_CONFIRMATION_USD
    correction_model: str = field(default_factory=lambda: config.CORRECTION_MODEL)
    translation_model: str = field(default_factory=lambda: config.TRANSLATION_MODEL)
    helper_model: str = field(default_factory=lambda: config.HELPER_MODEL)
    gemini_cache_only: bool = False  # no Gemini call; uncached chunks go to faster-whisper
    refusal_fallback: bool = True
    name: str = ""  # work/<name>/ and <name>.<lang>.srt; default: the source file's stem

    @property
    def video_id(self) -> str:
        return self.name or Path(self.source).stem

    @property
    def work_dir(self) -> Path:
        return Path(self.work_root) / self.video_id


@dataclass
class StepRecord:
    step: str
    output: str
    skipped: bool
    cost_usd: float  # Claude, spent in this run (0 when skipped)
    seconds: float
    stt_cost_usd: float = 0.0  # speech recognition service, list price, spent in this run
    detail: str = ""


@dataclass
class RunReport:
    steps: list[StepRecord] = field(default_factory=list)
    estimate_usd: float | None = None
    fallbacks: list[str] = field(default_factory=list)
    subtitles: dict[str, dict] = field(default_factory=dict)  # file name -> SubtitleReport.as_dict()

    @property
    def cost_usd(self) -> float:
        return round(sum(s.cost_usd for s in self.steps), 6)

    @property
    def stt_cost_usd(self) -> float:
        return round(sum(s.stt_cost_usd for s in self.steps), 6)


# --- stamps ---------------------------------------------------------------------


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stamp_path(output: Path) -> Path:
    return output.with_name(output.name + ".stamp.json")


def _stamp(inputs: dict[str, str]) -> str:
    return json.dumps(inputs, sort_keys=True, ensure_ascii=False)


def up_to_date(output: Path, inputs: dict[str, str]) -> bool:
    stamp = _stamp_path(output)
    return output.is_file() and stamp.is_file() and stamp.read_text(encoding="utf-8") == _stamp(inputs)


def write_stamp(output: Path, inputs: dict[str, str]) -> None:
    _stamp_path(output).write_text(_stamp(inputs), encoding="utf-8")


# --- engines ----------------------------------------------------------------------


def make_stt(engine: str, cache_dir: Path, progress: Callable[[ProgressEvent], None],
             gemini_cache_only: bool = False):
    """The transcriber of a non-AssemblyAI engine; for gemini(+align) the chunked Gemini engine."""
    if engine == "faster-whisper":
        from talk_autopsy.stt.faster_whisper import FasterWhisperTranscriber
        return FasterWhisperTranscriber(cpu_threads=os.cpu_count() or 0)
    if engine in ("gemini", "gemini+align"):
        from talk_autopsy.stt.chunked import ChunkedGeminiTranscriber
        return ChunkedGeminiTranscriber(cache_dir=cache_dir, progress=progress, cache_only=gemini_cache_only)
    raise ValueError(f"unknown engine {engine!r}; known: {', '.join(ENGINES)}")


def make_assemblyai(glossary_prompt: bool):
    from talk_autopsy.stt.assemblyai import AssemblyAITranscriber
    return AssemblyAITranscriber(glossary_prompt=glossary_prompt)


def _gemini_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def check_keys(settings: RunSettings) -> None:
    """Raise ConfigurationError now for any key a selected step will need."""
    steps = set(settings.steps)
    if CLAUDE_STEPS & steps:
        config.require_api_key()
    if settings.engine == "assemblyai" and steps & {"stt", "stt2"}:
        config.require_key("ASSEMBLYAI_API_KEY")
    if "stt" in steps and settings.engine.startswith("gemini") and not settings.gemini_cache_only:
        config.require_key("GEMINI_API_KEY")


def _copy_transcript(source: Path, out: Path, note: str) -> Transcript:
    """`source` passed through to `out` with a note in meta (a step that had nothing to add)."""
    transcript = load_transcript(source)
    transcript.meta["passed_through"] = note
    transcript.meta["cost_usd"] = 0.0
    save_transcript(transcript, out)
    return transcript


# --- the run ----------------------------------------------------------------------


def run(settings: RunSettings, progress: Callable[[ProgressEvent], None] | None = None) -> RunReport:
    emit_to = progress or (lambda event: None)
    steps = [s for s in STEPS if s in settings.steps]
    if settings.engine not in ENGINES:
        raise ValueError(f"unknown engine {settings.engine!r}; known: {', '.join(ENGINES)}")
    source = Path(settings.source)
    if not source.is_file():
        raise FileNotFoundError(f"No such file: {source}")
    check_keys(settings)
    lang = config.check_lang(settings.target_lang)
    work = settings.work_dir
    work.mkdir(parents=True, exist_ok=True)
    user_glossary = load_glossary(settings.glossary) if settings.glossary else None
    user_glossary_hash = (_text_hash(Path(settings.glossary).read_text(encoding="utf-8"))
                          if settings.glossary else "")
    started = time.perf_counter()
    report = RunReport()

    def emit(event: str, step: str, detail: str = "") -> None:
        emit_to(ProgressEvent(step, event, 0, len(steps), 0.0, 0.0, round(time.perf_counter() - started, 1),
                              "pipeline", detail))

    # meta.json: the context the Claude steps see; a local file's name is not context
    audio, flac, meta_path = work / "audio.wav", work / "audio.flac", work / "meta.json"
    meta = (json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file()
            else {"title": "", "description": "", "chapters": []})
    if settings.context:
        meta["description"] = settings.context
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + NL, encoding="utf-8")
    context = blocks.load_context(meta_path)
    context_hash = _text_hash(context.text())

    raw, raw2 = work / "raw.json", work / "raw2.json"
    aligned, fixed = work / "aligned.json", work / "fixed.json"
    auto_glossary_path, glossary_path = work / "glossary.auto.md", work / "glossary.md"
    translated = work / f"{lang}.json"

    def need(path: Path, step: str) -> Path:
        if not path.is_file():
            raise PipelineError(f"step {step} needs {path}; run the step that writes it first")
        return path

    def step_done(step: str, output: Path, skipped: bool, cost: float, t0: float, stt_cost: float = 0.0,
                  detail: str = "") -> None:
        report.steps.append(StepRecord(step, str(output), skipped, round(cost, 6),
                                       round(time.perf_counter() - t0, 1), round(stt_cost, 6), detail))
        spent = f", {cost:.4f} USD" if cost else ""
        spent += f", speech recognition {stt_cost:.4f} USD" if stt_cost else ""
        emit("step-skipped" if skipped else "step-done", step,
             f"{output.name}" + ("" if skipped else spent) + (f"; {detail}" if detail else ""))

    def current_glossary() -> tuple[Glossary, str]:
        """The run's glossary: glossary.md once the glossary step wrote it, else the user's (or none)."""
        if glossary_path.is_file() and ("glossary" in steps or settings.glossary is None):
            text = glossary_path.read_text(encoding="utf-8")
            return load_glossary(glossary_path), _text_hash(text)
        return (user_glossary or Glossary()), user_glossary_hash

    # --- stt: first pass ------------------------------------------------------------
    if "stt" in steps:
        t0 = time.perf_counter()
        source_stamp = {"source": str(source.resolve()), "size": str(source.stat().st_size),
                        "mtime": str(source.stat().st_mtime_ns)}
        if not up_to_date(audio, source_stamp):
            emit("step-start", "audio", "extracting 16 kHz mono audio")
            ingest.extract_audio(source, audio)
            write_stamp(audio, source_stamp)
        audio_hash = file_sha256(audio)
        inputs = {"audio": audio_hash, "engine": settings.engine,
                  "gemini_cache_only": str(settings.gemini_cache_only)}
        skipped = up_to_date(raw, inputs)
        stt_cost, detail = 0.0, ""
        if not skipped:
            transcript = _first_pass(settings, audio, flac, audio_hash, work, emit, emit_to)
            save_transcript(transcript, raw)
            write_stamp(raw, inputs)
            stt_cost = float(transcript.meta.get("cost_usd") or 0.0)
            if transcript.meta.get("fallback"):
                fb = transcript.meta["fallback"]
                report.fallbacks.append(f"stt: {fb['from']} -> {fb['to']} ({fb['reason'][:120]})")
                detail = f"fallback to {fb['to']}"
        elif load_transcript(raw).meta.get("fallback"):
            report.fallbacks.append("stt: fallback (earlier run)")
        step_done("stt", raw, skipped, 0.0, t0, stt_cost, detail)

    # --- cost estimate of the Claude steps, from the first transcript ----------------
    if CLAUDE_STEPS & set(steps):
        base_path = next((p for p in (raw2, raw) if p.is_file()), None)
        if base_path is None:
            base_path = need(aligned, steps[0])
        base = load_transcript(base_path)
        glossary_now, _ = current_glossary()
        total = 0.0
        if "glossary" in steps:
            e = glossary_auto.estimate(base, lang, settings.context, settings.helper_model)
            emit("estimate", "glossary", e.line())
            total += e.usd
        if "fix" in steps:
            e = correct.estimate(base, glossary_now, context, settings.correction_model)
            emit("estimate", "fix", e.line())
            total += e.usd
        if "translate" in steps:
            e = translate.estimate(base, glossary_now, lang, context, settings.translation_model)
            emit("estimate", "translate", e.line())
            total += e.usd
        report.estimate_usd = round(total, 4)
        if total > settings.max_cost_usd:
            raise PipelineError(f"the Claude steps are estimated at {total:.2f} USD, above the limit of "
                                f"{settings.max_cost_usd:.2f} USD; raise --max-cost to confirm")

    # --- glossary ----------------------------------------------------------------------
    if "glossary" in steps:
        t0 = time.perf_counter()
        inputs = {"raw": file_sha256(need(raw, "glossary")), "context": _text_hash(settings.context),
                  "user_glossary": user_glossary_hash, "model": settings.helper_model, "lang": lang,
                  "prompt": glossary_auto.PROMPT_VERSION}
        skipped = up_to_date(glossary_path, inputs)
        cost, detail = 0.0, ""
        if not skipped:
            emit("step-start", "glossary", f"{glossary_auto.PROMPT_VERSION}, {settings.helper_model}")
            result = glossary_auto.generate(load_transcript(raw), lang, settings.context,
                                            model=settings.helper_model)
            auto_glossary_path.write_text(result.markdown, encoding="utf-8", newline=NL)
            merged = glossary_auto.merge(result.glossary, user_glossary)
            languages = sorted({lang, *merged.languages()})
            header = (f"<!-- glossary of this run: the user's glossary ({Path(settings.glossary).name}) "
                      "first, "
                      f"then the auto-glossary of {result.model}; drafted translations are suggestions -->"
                      if settings.glossary else
                      f"<!-- glossary of this run: auto-glossary of {result.model}; "
                      "drafted translations are suggestions -->")
            glossary_path.write_text(header + NL + NL + to_markdown(merged, languages), encoding="utf-8",
                                     newline=NL)
            write_stamp(glossary_path, inputs)
            cost = result.cost_usd
            detail = (f"{len(result.glossary.entries)} auto terms, {len(result.heard_as)} with corrected "
                      f"mis-recognitions, {len(merged.entries)} in the run glossary")
        step_done("glossary", glossary_path, skipped, cost, t0, detail=detail)

    # --- stt2: second pass with key terms ---------------------------------------------
    if "stt2" in steps:
        t0 = time.perf_counter()
        glossary_now, glossary_hash = current_glossary()
        first = load_transcript(need(raw, "stt2"))
        inputs = {"audio": file_sha256(need(audio, "stt2")), "raw": file_sha256(raw),
                  "glossary": glossary_hash, "engine": settings.engine}
        skipped = up_to_date(raw2, inputs)
        stt_cost, detail = 0.0, ""
        if not skipped:
            if settings.engine != "assemblyai":
                _copy_transcript(raw, raw2, f"{settings.engine} has no key-term pass")
                detail = "passed through (engine has no key-term pass)"
            elif first.meta.get("fallback"):
                _copy_transcript(raw, raw2, "the first pass used a fallback engine")
                detail = "passed through (first pass used a fallback)"
            elif not glossary_now.entries:
                _copy_transcript(raw, raw2, "no glossary terms")
                detail = "passed through (no glossary terms)"
            else:
                emit("step-start", "stt2", f"AssemblyAI with {len(glossary_now.entries)} key terms")
                try:
                    if not flac.is_file():
                        ingest.compress_audio(audio, flac)
                    transcript = make_assemblyai(glossary_prompt=True).transcribe(flac, glossary_now)
                except Exception as e:  # the service failed: keep the first pass, say so
                    logger.warning("stt2: AssemblyAI failed (%s); keeping the first pass", e)
                    _copy_transcript(raw, raw2, f"AssemblyAI failed: {e}")
                    report.fallbacks.append(f"stt2: kept the first pass ({str(e)[:120]})")
                    detail = "passed through (AssemblyAI failed)"
                else:
                    transcript.meta["glossary"] = glossary_path.name if glossary_path.is_file() else ""
                    save_transcript(transcript, raw2)
                    stt_cost = float(transcript.meta.get("cost_usd") or 0.0)
            write_stamp(raw2, inputs)
        step_done("stt2", raw2, skipped, 0.0, t0, stt_cost, detail)

    # --- align (only model-estimated times) ---------------------------------------------
    recognized = raw2 if raw2.is_file() and ("stt2" in steps or "stt" not in steps) else raw
    if "align" in steps:
        t0 = time.perf_counter()
        inputs = {"input": file_sha256(need(recognized, "align")), "audio": file_sha256(need(audio, "align"))}
        skipped = up_to_date(aligned, inputs)
        if not skipped:
            transcript = load_transcript(recognized)
            if transcript.meta.get("timestamps") == "model-estimated":
                emit("step-start", "align", f"{len(transcript.segments)} segments")
                transcript = align_module.align(transcript, audio, align_module.Wav2Vec2Emitter())
            else:
                emit("step-start", "align", "times already measured; passing the transcript through")
            save_transcript(transcript, aligned)
            write_stamp(aligned, inputs)
        step_done("align", aligned, skipped, 0.0, t0)

    english = aligned if "align" in steps or (aligned.is_file() and not recognized.is_file()) else recognized

    if "fix" in steps:
        t0 = time.perf_counter()
        glossary_now, glossary_hash = current_glossary()
        inputs = {"input": file_sha256(need(english, "fix")), "glossary": glossary_hash,
                  "context": context_hash, "model": settings.correction_model,
                  "prompt": correct.PROMPT_VERSION}
        skipped = up_to_date(fixed, inputs)
        cost = 0.0
        if not skipped:
            emit("step-start", "fix", f"{correct.PROMPT_VERSION}, {settings.correction_model}")
            result = correct.correct(load_transcript(english), glossary_now, context,
                                     model=settings.correction_model,
                                     refusal_fallback=settings.refusal_fallback,
                                     progress=lambda n, total: emit("block-done", "fix",
                                                                    f"block {n}/{total}"))
            save_transcript(result, fixed)
            write_stamp(fixed, inputs)
            cost = result.meta["correction"]["cost_usd"]
        step_done("fix", fixed, skipped, cost, t0)

    if "translate" in steps:
        t0 = time.perf_counter()
        glossary_now, glossary_hash = current_glossary()
        source_file = fixed if fixed.is_file() else need(english, "translate")
        inputs = {"input": file_sha256(source_file), "glossary": glossary_hash, "context": context_hash,
                  "lang": lang, "model": settings.translation_model, "prompt": translate.PROMPT_VERSION}
        skipped = up_to_date(translated, inputs)
        cost = 0.0
        if not skipped:
            emit("step-start", "translate", f"{translate.PROMPT_VERSION}, {settings.translation_model}")
            result = translate.translate(load_transcript(source_file), glossary_now, lang, context=context,
                                         model=settings.translation_model,
                                         refusal_fallback=settings.refusal_fallback,
                                         progress=lambda n, total: emit("block-done", "translate",
                                                                        f"block {n}/{total}"))
            save_transcript(result, translated)
            write_stamp(translated, inputs)
            cost = result.meta["translation"]["cost_usd"]
        step_done("translate", translated, skipped, cost, t0)

    if "srt" in steps:
        t0 = time.perf_counter()
        glossary_now, _ = current_glossary()
        written = []
        english_final = fixed if fixed.is_file() else english
        for transcript_path, suffix in ((translated, lang), (english_final, "en")):
            if not transcript_path.is_file():
                continue
            out = work / f"{settings.video_id}.{suffix}.srt"
            result = subtitles.write_srt(load_transcript(transcript_path), out, glossary_now)
            written.append(out.name)
            report.subtitles[out.name] = result.as_dict()
            emit("srt", "srt", f"{out.name} + .ass: {result.line()}")
        if not written:
            raise PipelineError("step srt found no transcript to write; run stt first")
        step_done("srt", work / written[0], False, 0.0, t0)

    emit("done", "pipeline",
         f"{report.cost_usd:.4f} USD Claude, {report.stt_cost_usd:.4f} USD speech recognition")
    return report


def _first_pass(settings: RunSettings, audio: Path, flac: Path, audio_hash: str, work: Path, emit,
                emit_to) -> Transcript:
    """The stt step: the chosen engine; for AssemblyAI the fallback chain on a service failure."""
    if settings.engine != "assemblyai":
        emit("step-start", "stt", settings.engine)
        engine = make_stt(settings.engine, work / "chunks", emit_to, settings.gemini_cache_only)
        return engine.transcribe(audio, None)
    emit("step-start", "stt", "AssemblyAI, compressed audio upload")
    try:
        flac_stamp = {"audio": audio_hash}
        if not up_to_date(flac, flac_stamp):
            ingest.compress_audio(audio, flac)
            write_stamp(flac, flac_stamp)
        return make_assemblyai(glossary_prompt=False).transcribe(flac, None)
    except Exception as e:  # service, network or upload failure: the next engine takes over, visibly
        reason = f"{type(e).__name__}: {e}"
        to = "gemini+align" if _gemini_available() or settings.gemini_cache_only else "faster-whisper"
        logger.warning("stt: AssemblyAI failed (%s); falling back to %s", reason, to)
        emit("fallback", "stt", f"AssemblyAI failed ({reason[:120]}); using {to}")
        fallback = make_stt(to, work / "chunks", emit_to, settings.gemini_cache_only)
        transcript = fallback.transcribe(audio, None)
        transcript.meta["fallback"] = {"from": "assemblyai", "to": to, "reason": reason}
        return transcript


def cost_lines(report: RunReport) -> list[str]:
    """The cost report (F10): one line per step and the totals."""
    lines = []
    for r in report.steps:
        timing = "skipped (up to date)" if r.skipped else f"{r.seconds:7.1f} s"
        stt = f" + {r.stt_cost_usd:.4f} USD speech recognition" if r.stt_cost_usd else ""
        lines.append(f"  {r.step:<10} {timing}   {r.cost_usd:.4f} USD{stt}   {Path(r.output).name}"
                     + (f"   ({r.detail})" if r.detail else ""))
    if report.estimate_usd is not None:
        lines.append(f"  estimate of the Claude steps before the run: {report.estimate_usd:.4f} USD")
    lines.append(f"  total spent in this run: {report.cost_usd:.4f} USD Claude, "
                 f"{report.stt_cost_usd:.4f} USD speech recognition (list price)")
    for f in report.fallbacks:
        lines.append(f"  fallback: {f}")
    return lines

