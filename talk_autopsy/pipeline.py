"""
The pipeline: one video through the selected steps, each reading and writing
its own file in `work/<video-id>/`.

    source -> audio.wav + meta.json
      stt        -> raw.json        (engine: faster-whisper | gemini | gemini+align)
      align      -> aligned.json    (forced alignment; a transcript with measured times passes through)
      fix        -> fixed.json      (Claude correction)
      translate  -> <lang>.json     (Claude translation)
      srt        -> <video-id>.<lang>.srt and <video-id>.en.srt

- **Skip when unchanged.** Every output has a stamp file `<output>.stamp.json`
  with the hashes of its inputs and the settings that shape it (engine, model,
  prompt version, glossary, language). A step whose output exists with a
  matching stamp is skipped; anything else reruns it. For `gemini+align` the stt
  step also has the per-chunk cache (stt/chunked.py) in `chunks/`.
- **Fail fast.** Keys a selected step needs (Anthropic for fix and translate,
  Gemini for a Gemini engine) are checked before any work.
- **Cost.** Before the first Claude step the estimate of all Claude steps is
  printed through the progress callback; above `max_cost_usd` the run stops
  before spending. At the end `RunReport` has the cost of every step (F10).
- **Progress** (F13): `ProgressEvent`s, as in stt/chunked.py, for every step.

No CLI input or output here, so a web UI can call `run` the same way.
"""

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from talk_autopsy import align as align_module
from talk_autopsy import blocks, config, correct, ingest, subtitles, translate
from talk_autopsy.glossary import Glossary, load_glossary
from talk_autopsy.segments import load_transcript, save_transcript
from talk_autopsy.stt.chunked import ProgressEvent, file_sha256

STEPS = ("stt", "align", "fix", "translate", "srt")
CLAUDE_STEPS = {"fix", "translate"}
ENGINES = ("faster-whisper", "gemini", "gemini+align")
DEFAULT_ENGINE = "gemini+align"


class PipelineError(RuntimeError):
    """A run cannot go on: a missing input, a cost above the limit, a failed step."""


@dataclass
class RunSettings:
    source: Path
    target_lang: str
    steps: list[str]
    engine: str = DEFAULT_ENGINE
    glossary: Path | None = None
    work_root: Path = config.WORK_DIR
    max_cost_usd: float = config.COST_CONFIRMATION_USD
    correction_model: str = field(default_factory=lambda: config.CORRECTION_MODEL)
    translation_model: str = field(default_factory=lambda: config.TRANSLATION_MODEL)

    @property
    def video_id(self) -> str:
        return Path(self.source).stem

    @property
    def work_dir(self) -> Path:
        return Path(self.work_root) / self.video_id


@dataclass
class StepRecord:
    step: str
    output: str
    skipped: bool
    cost_usd: float  # spent in this run (0 when skipped)
    seconds: float


@dataclass
class RunReport:
    steps: list[StepRecord] = field(default_factory=list)
    estimate_usd: float | None = None

    @property
    def cost_usd(self) -> float:
        return round(sum(s.cost_usd for s in self.steps), 6)


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


def make_stt(engine: str, cache_dir: Path, progress: Callable[[ProgressEvent], None]):
    """The stt step's transcriber; for gemini+align the chunked Gemini engine (the align step follows)."""
    if engine == "faster-whisper":
        import os

        from talk_autopsy.stt.faster_whisper import FasterWhisperTranscriber
        return FasterWhisperTranscriber(cpu_threads=os.cpu_count() or 0)
    if engine in ("gemini", "gemini+align"):
        from talk_autopsy.stt.chunked import ChunkedGeminiTranscriber
        return ChunkedGeminiTranscriber(cache_dir=cache_dir, progress=progress)
    raise ValueError(f"unknown engine {engine!r}; known: {', '.join(ENGINES)}")


def check_keys(settings: RunSettings) -> None:
    """Raise ConfigurationError now for any key a selected step will need."""
    if CLAUDE_STEPS & set(settings.steps):
        config.require_api_key()
    if "stt" in settings.steps and settings.engine.startswith("gemini"):
        config.require_key("GEMINI_API_KEY")


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
    glossary = load_glossary(settings.glossary) if settings.glossary else Glossary()
    glossary_text = Path(settings.glossary).read_text(encoding="utf-8") if settings.glossary else ""
    glossary_hash = _text_hash(glossary_text) if settings.glossary else ""
    started = time.perf_counter()
    report = RunReport()

    def emit(event: str, step: str, detail: str = "") -> None:
        emit_to(ProgressEvent(step, event, 0, len(steps), 0.0, 0.0, round(time.perf_counter() - started, 1),
                              "pipeline", detail))

    audio, meta_path = work / "audio.wav", work / "meta.json"
    if not meta_path.is_file():
        meta_path.write_text(json.dumps({"title": settings.video_id, "description": "", "chapters": []},
                                        indent=2) + "\n", encoding="utf-8")
    context = blocks.load_context(meta_path)
    context_hash = _text_hash(context.text())
    raw, aligned, fixed = work / "raw.json", work / "aligned.json", work / "fixed.json"
    translated = work / f"{lang}.json"

    def need(path: Path, step: str) -> Path:
        if not path.is_file():
            raise PipelineError(f"step {step} needs {path}; run the step that writes it first")
        return path

    def step_done(step: str, output: Path, skipped: bool, cost: float, t0: float) -> None:
        report.steps.append(StepRecord(step, str(output), skipped, round(cost, 6),
                                       round(time.perf_counter() - t0, 1)))
        emit("step-skipped" if skipped else "step-done", step,
             f"{output.name}" + ("" if skipped else f", {cost:.4f} USD"))

    if "stt" in steps:
        t0 = time.perf_counter()
        source_stamp = {"source": str(source.resolve()), "size": str(source.stat().st_size),
                        "mtime": str(source.stat().st_mtime_ns)}
        if not up_to_date(audio, source_stamp):
            emit("step-start", "audio", "extracting 16 kHz mono audio")
            ingest.extract_audio(source, audio)
            write_stamp(audio, source_stamp)
        inputs = {"audio": file_sha256(audio), "engine": settings.engine}
        skipped = up_to_date(raw, inputs)
        if not skipped:
            emit("step-start", "stt", settings.engine)
            transcript = make_stt(settings.engine, work / "chunks", emit_to).transcribe(audio, None)
            save_transcript(transcript, raw)
            write_stamp(raw, inputs)
        step_done("stt", raw, skipped, 0.0, t0)

    if "align" in steps:
        t0 = time.perf_counter()
        inputs = {"raw": file_sha256(need(raw, "align")), "audio": file_sha256(need(audio, "align"))}
        skipped = up_to_date(aligned, inputs)
        if not skipped:
            transcript = load_transcript(raw)
            if transcript.meta.get("timestamps") == "model-estimated":
                emit("step-start", "align", f"{len(transcript.segments)} segments")
                transcript = align_module.align(transcript, audio, align_module.Wav2Vec2Emitter())
            else:
                emit("step-start", "align", "times already measured; passing the transcript through")
            save_transcript(transcript, aligned)
            write_stamp(aligned, inputs)
        step_done("align", aligned, skipped, 0.0, t0)

    english = aligned if aligned.is_file() else raw
    if CLAUDE_STEPS & set(steps):
        base = load_transcript(need(english, "fix" if "fix" in steps else "translate"))
        total = 0.0
        if "fix" in steps:
            e = correct.estimate(base, glossary, context, settings.correction_model)
            emit("estimate", "fix", e.line())
            total += e.usd
        if "translate" in steps:
            e = translate.estimate(base, glossary, lang, context, settings.translation_model)
            emit("estimate", "translate", e.line())
            total += e.usd
        report.estimate_usd = round(total, 4)
        pending = ("fix" in steps and not up_to_date(fixed, _fix_inputs(english, glossary_hash, context_hash,
                                                                        settings))) or "translate" in steps
        if pending and total > settings.max_cost_usd:
            raise PipelineError(f"the Claude steps are estimated at {total:.2f} USD, above the limit of "
                                f"{settings.max_cost_usd:.2f} USD; raise --max-cost to confirm")

    if "fix" in steps:
        t0 = time.perf_counter()
        inputs = _fix_inputs(need(english, "fix"), glossary_hash, context_hash, settings)
        skipped = up_to_date(fixed, inputs)
        cost = 0.0
        if not skipped:
            emit("step-start", "fix", f"{correct.PROMPT_VERSION}, {settings.correction_model}")
            result = correct.correct(load_transcript(english), glossary, context,
                                     model=settings.correction_model,
                                     progress=lambda n, total: emit("block-done", "fix",
                                                                    f"block {n}/{total}"))
            save_transcript(result, fixed)
            write_stamp(fixed, inputs)
            cost = result.meta["correction"]["cost_usd"]
        step_done("fix", fixed, skipped, cost, t0)

    if "translate" in steps:
        t0 = time.perf_counter()
        source_file = fixed if fixed.is_file() else need(english, "translate")
        inputs = {"input": file_sha256(source_file), "glossary": glossary_hash, "context": context_hash,
                  "lang": lang, "model": settings.translation_model, "prompt": translate.PROMPT_VERSION}
        skipped = up_to_date(translated, inputs)
        cost = 0.0
        if not skipped:
            emit("step-start", "translate", f"{translate.PROMPT_VERSION}, {settings.translation_model}")
            result = translate.translate(load_transcript(source_file), glossary, lang, context=context,
                                         model=settings.translation_model,
                                         progress=lambda n, total: emit("block-done", "translate",
                                                                        f"block {n}/{total}"))
            save_transcript(result, translated)
            write_stamp(translated, inputs)
            cost = result.meta["translation"]["cost_usd"]
        step_done("translate", translated, skipped, cost, t0)

    if "srt" in steps:
        t0 = time.perf_counter()
        written = []
        for transcript_path, suffix in ((translated, lang), (fixed if fixed.is_file() else english, "en")):
            if not transcript_path.is_file():
                continue
            out = work / f"{settings.video_id}.{suffix}.srt"
            result = subtitles.write_srt(load_transcript(transcript_path), out, glossary)
            written.append(out.name)
            emit("srt", "srt", f"{out.name}: {result.cues} cues, max {result.reading.max_cps:.1f} cps")
        if not written:
            raise PipelineError("step srt found no transcript to write; run stt first")
        step_done("srt", work / written[0], False, 0.0, t0)

    emit("done", "pipeline", f"{report.cost_usd:.4f} USD")
    return report


def _fix_inputs(english: Path, glossary_hash: str, context_hash: str,
                settings: RunSettings) -> dict[str, str]:
    return {"input": file_sha256(english) if english.is_file() else "", "glossary": glossary_hash,
            "context": context_hash, "model": settings.correction_model, "prompt": correct.PROMPT_VERSION}


def cost_lines(report: RunReport) -> list[str]:
    """The cost report (F10): one line per step and the total."""
    lines = [f"  {r.step:<10} {'skipped (up to date)' if r.skipped else f'{r.seconds:7.1f} s'}   "
             f"{r.cost_usd:.4f} USD   {Path(r.output).name}" for r in report.steps]
    if report.estimate_usd is not None:
        lines.append(f"  estimate of the Claude steps before the run: {report.estimate_usd:.4f} USD")
    lines.append(f"  total spent in this run: {report.cost_usd:.4f} USD")
    return lines
