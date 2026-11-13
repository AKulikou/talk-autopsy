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
      translate  -> <lang>.json     Claude translation (translation-v2.1) of sentence units (units.py)
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
  with the hashes of its inputs and the settings that shape it, including
  `code`: a hash of the step's prompt file and of the modules that validate
  or post-process its output (`code_hash`, `STEP_CODE`). A step whose output
  exists with a matching stamp is skipped; anything else reruns it, so a
  prompt or validator fix invalidates the old results. A correction or
  translation with failed blocks or reverted segments is never up to date (its
  stamp names them, `write_result_stamp`): the next run does the step again.
  Only blocks whose request would differ, or that failed or had a segment
  reverted, are sent; every other block's accepted answer is reused, also
  when this run's correction changed other blocks of the translation's input
  (`reusable_answers`; pipeline-v3.3 for failed translation blocks, v3.4 for
  the rest).
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
  Since pipeline-v3.1 (observability only; docs/decisions.md) the events of
  estimates, finished steps and finished correction and translation blocks are
  `PipelineEvent`s: the same fields and text, plus `data` with numbers (cost,
  tokens, seconds) and a block's text (corrected segments and edits, or
  translated units with their source). The block text comes from
  `structured_call.result_observer`; model requests and outputs are unchanged,
  and a progress callback that raises is logged, never fails the run.

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
from talk_autopsy import (
    blocks,
    config,
    correct,
    glossary_auto,
    ingest,
    subtitles,
    term_policy,
    translate,
    units,
    youtube,
)
from talk_autopsy.core import structured_call
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


@dataclass(frozen=True)
class PipelineEvent(ProgressEvent):
    """A ProgressEvent with numbers and text for a display (pipeline-v3.1, observability only)."""

    data: dict = field(default_factory=dict, compare=False, hash=False)


class _BlockAnswers:
    """The accepted answers of one step's blocks, in order, as structured_call reports them."""

    def __init__(self) -> None:
        self.pending: list = []
        self.reverted: list[int] = []  # segments of the pending answer put back to their input

    def __call__(self, label: str, result, reverted: list[int] | None = None) -> None:
        self.pending.append(result)
        self.reverted = list(reverted or [])

    def take(self):
        """The answer of the block just finished, or None if it failed validation."""
        answer = self.pending[-1] if self.pending else None
        self.pending.clear()
        self.reverted = []
        return answer


def _usage_numbers(usage: dict | None) -> dict | None:
    if not usage:
        return None
    return {"input": int(usage.get("input_tokens", 0)), "output": int(usage.get("output_tokens", 0)),
            "cache_write": int(usage.get("cache_creation_input_tokens", 0)),
            "cache_read": int(usage.get("cache_read_input_tokens", 0))}


def _estimate_data(e: blocks.Estimate, part: str) -> dict:
    return {"usd": e.usd, "blocks": e.blocks, "input_tokens": e.input_tokens,
            "output_tokens": e.output_tokens, "part": part}


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
    correction_model: str = field(default_factory=config.correction_model)
    translation_model: str = field(default_factory=config.translation_model)
    translation_version: str = field(default_factory=config.translation_version)  # translation-v2.1 | -v3
    helper_model: str = field(default_factory=config.helper_model)
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


PACKAGE_DIR = Path(__file__).resolve().parent
CRLF, LF = bytes([13, 10]), bytes([10])
# The modules whose code shapes each step's output (validators, post-processing, segmentation).
# metrics.py is in the Claude steps' list because their validators count terms with it.
STEP_CODE = {
    "stt": ("stt/assemblyai.py", "stt/base.py", "stt/chunked.py", "stt/gemini.py", "stt/faster_whisper.py",
            "align.py"),
    "glossary": ("glossary_auto.py", "glossary.py", "metrics.py"),
    "stt2": ("stt/assemblyai.py", "stt/base.py"),
    "align": ("align.py",),
    "fix": ("correct.py", "blocks.py", "metrics.py", "segments.py"),
    "translate": ("translate.py", "blocks.py", "units.py", "metrics.py", "segments.py", "term_policy.py"),
}


def code_hash(step: str, prompt: str | None = None) -> str:
    """
    SHA-256 (first 16 hex digits) of the step's prompt file (`prompts/<prompt>.md`)
    and of its modules (`STEP_CODE`), with line endings normalized, so the stamp
    of a checkout with CRLF files equals that of one with LF files.
    """
    digest = hashlib.sha256()
    files = [PACKAGE_DIR / "prompts" / f"{prompt}.md"] if prompt else []
    files += [PACKAGE_DIR / name for name in STEP_CODE[step]]
    for path in files:
        digest.update(path.name.encode("utf-8") + bytes(1))
        digest.update(path.read_bytes().replace(CRLF, LF))
    return digest.hexdigest()[:16]


def _stamp_path(output: Path) -> Path:
    return output.with_name(output.name + ".stamp.json")


def _stamp(inputs: dict[str, str]) -> str:
    return json.dumps(inputs, sort_keys=True, ensure_ascii=False)


def up_to_date(output: Path, inputs: dict[str, str]) -> bool:
    stamp = _stamp_path(output)
    return output.is_file() and stamp.is_file() and stamp.read_text(encoding="utf-8") == _stamp(inputs)


def write_stamp(output: Path, inputs: dict[str, str]) -> None:
    _stamp_path(output).write_text(_stamp(inputs), encoding="utf-8")


INCOMPLETE = "incomplete"  # stamp key of a correction or translation with failed blocks (pipeline-v3.3)


def write_result_stamp(output: Path, inputs: dict[str, str], failed_blocks: list[dict],
                       reverted: list[dict] | None = None) -> None:
    """
    The stamp of a correction or translation result. With failed blocks or
    reverted segments it also names them, so it never equals the inputs' stamp:
    the step is not up to date and runs again next time (a block that fell back,
    or a segment put back to its input, is not a finished result).
    """
    if not failed_blocks and not reverted:
        write_stamp(output, inputs)
        return
    parts = []
    if failed_blocks:
        parts.append("failed blocks " + ", ".join(str(b["block"]) for b in failed_blocks))
    if reverted:
        parts.append("reverted segments in blocks " + ", ".join(str(b["block"]) for b in reverted))
    write_stamp(output, {**inputs, INCOMPLETE: "; ".join(parts)})


def reusable_answers(output: Path, inputs: dict[str, str], stage: str, current: Transcript,
                     earlier_inputs: dict[str, Transcript], plan: Callable[[Transcript], list]
                     ) -> tuple[dict[int, str], Transcript] | None:
    """
    What an earlier result can give this run without a request: segment (or unit)
    id -> text of every block whose request would be the same now and that the
    earlier run accepted whole (no failed block, no reverted segment), and the
    earlier result. None when nothing can be reused.

    The earlier result counts when its stamp equals `inputs` apart from the
    INCOMPLETE marker and the input file's hash, and that hash is one of
    `earlier_inputs` (sha256 -> the transcript it was made from: the current input,
    and for the translation the correction this run just replaced). `plan(input)`
    gives the step's blocks with the user text each would send; the rest of a
    request (prompt, glossary, context, model) is fixed by the stamp's other keys.
    """
    stamp = _stamp_path(output)
    if not (output.is_file() and stamp.is_file()):
        return None
    try:
        earlier = json.loads(stamp.read_text(encoding="utf-8"))
        if not isinstance(earlier, dict):
            return None
        earlier.pop(INCOMPLETE, None)
        source = earlier_inputs.get(earlier.pop("input", None))
        if source is None or earlier != {k: v for k, v in inputs.items() if k != "input"}:
            return None
        result = load_transcript(output)
        meta = result.meta[stage]
        bad = {int(i) for b in meta.get("failed_blocks") or [] for i in b["ids"]}
        bad |= {int(i) for b in meta.get("reverted_segments") or [] for i in b["ids"]}
        texts = {seg.id: seg.text for seg in result.segments}
        accepted = {user: block for block, user in plan(source)
                    if not any(seg.id in bad for seg in block.segments)}
        done: dict[int, str] = {}
        for block, user in plan(current):
            old = accepted.get(user)
            if old is not None:
                pairs = zip(old.segments, block.segments, strict=True)
                done.update({new.id: texts[seg.id] for seg, new in pairs})
        return (done, result) if done else None
    except Exception:  # an unreadable earlier result: the step runs in full
        logger.exception("the earlier result %s cannot be reused; the step runs in full", output.name)
        return None


def _correction_plan(transcript: Transcript) -> list:
    return [(block, correct._user(block)) for block in blocks.make_blocks(transcript)]


def _translation_plan(transcript: Transcript) -> list:
    return [(block, translate._user(block)) for block in blocks.make_blocks(units.make_units(transcript))]


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


def make_assemblyai(glossary_prompt: bool, user_terms: list[str] | None = None, first_pass: str = ""):
    from talk_autopsy.stt.assemblyai import AssemblyAITranscriber
    return AssemblyAITranscriber(glossary_prompt=glossary_prompt, user_terms=user_terms or [],
                                 first_pass=first_pass)


def _require_alignment() -> None:
    """PipelineError with the install hint if forced alignment cannot run (no stack trace in the CLI)."""
    if not youtube.align_extra_installed():
        raise PipelineError(youtube.ALIGN_UNAVAILABLE)


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
    if settings.engine in ("gemini", "gemini+align") and "align" in steps:
        _require_alignment()  # before any Gemini call: its times are estimates and must be aligned
    lang = config.check_lang(settings.target_lang)
    work = settings.work_dir
    work.mkdir(parents=True, exist_ok=True)
    user_glossary = load_glossary(settings.glossary) if settings.glossary else None
    user_glossary_hash = (_text_hash(Path(settings.glossary).read_text(encoding="utf-8"))
                          if settings.glossary else "")
    started = time.perf_counter()
    report = RunReport()

    def emit(event: str, step: str, detail: str = "", data: dict | None = None) -> None:
        fields = (step, event, 0, len(steps), 0.0, 0.0, round(time.perf_counter() - started, 1), "pipeline",
                  detail)
        try:
            emit_to(ProgressEvent(*fields) if data is None else PipelineEvent(*fields, data=data))
        except Exception:  # a display problem never stops a run (pipeline-v3.1)
            logger.exception("progress callback failed on %s %s; the run goes on", step, event)

    def block_done(step: str, answers: _BlockAnswers, parts: list, sources: dict[int, str] | None,
                   n: int, total: int) -> None:
        """The block-done event of `fix` or `translate`, with the block's text when it was accepted."""
        data = None
        try:
            reverted = answers.reverted
            answer = answers.take()
            block = parts[n - 1] if 0 < n <= len(parts) else None
            data = {"position": n, "total": total, "failed": answer is None,
                    "ids": [seg.id for seg in block.segments] if block else []}
            if answer is not None and step == "fix":
                data["segments"] = [{"id": seg.id, "text": seg.text} for seg in answer.segments]
                data["edits"] = [{"id": e.id, "before": e.before, "after": e.after} for e in answer.edits]
            elif answer is not None:
                data["units"] = [{"id": seg.id, "text": translate.visible_text(seg.text),
                                  "source": (sources or {}).get(seg.id, "")} for seg in answer.segments]
            if block is not None and step == "translate":
                data["source_ids"] = {str(seg.id): seg.source_ids or [] for seg in block.segments}
            if answer is not None and reverted:  # kept as they were (pipeline-v3.4)
                data["reverted"] = reverted
        except Exception:
            logger.exception("block text of %s block %s not prepared; the run goes on", step, n)
            data = None
        emit("block-done", step, f"block {n}/{total}", data)

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
                  detail: str = "", tokens: dict | None = None) -> None:
        record = StepRecord(step, str(output), skipped, round(cost, 6), round(time.perf_counter() - t0, 1),
                            round(stt_cost, 6), detail)
        report.steps.append(record)
        spent = f", {cost:.4f} USD" if cost else ""
        spent += f", speech recognition {stt_cost:.4f} USD" if stt_cost else ""
        emit("step-skipped" if skipped else "step-done", step,
             f"{output.name}" + ("" if skipped else spent) + (f"; {detail}" if detail else ""),
             {"skipped": skipped, "seconds": record.seconds, "cost_usd": record.cost_usd,
              "stt_cost_usd": record.stt_cost_usd, "tokens": None if skipped else tokens})

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
                  "gemini_cache_only": str(settings.gemini_cache_only), "code": code_hash("stt")}
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
            emit("estimate", "glossary", e.line(), _estimate_data(e, "glossary"))
            total += e.usd
        if "fix" in steps:
            e = correct.estimate(base, glossary_now, context, settings.correction_model)
            emit("estimate", "fix", e.line(), _estimate_data(e, "fix"))
            total += e.usd
        if "translate" in steps:
            if settings.translation_version == translate.V3:
                e = term_policy.estimate(glossary_now, user_glossary, lang, settings.helper_model)
                emit("estimate", "translate", f"term classification: {e.line()}",
                     _estimate_data(e, "term_classification"))
                total += e.usd
            e = translate.estimate(base, glossary_now, lang, context, settings.translation_model,
                                   version=settings.translation_version)
            emit("estimate", "translate", e.line(), _estimate_data(e, "translate"))
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
                  "prompt": glossary_auto.PROMPT_VERSION,
                  "code": code_hash("glossary", glossary_auto.PROMPT_VERSION)}
        skipped = up_to_date(glossary_path, inputs)
        cost, detail, tokens = 0.0, "", None
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
            tokens = _usage_numbers(result.usage.as_dict())
            detail = (f"{len(result.glossary.entries)} auto terms, {len(result.heard_as)} with corrected "
                      f"mis-recognitions, {len(merged.entries)} in the run glossary")
        step_done("glossary", glossary_path, skipped, cost, t0, detail=detail, tokens=tokens)

    # --- stt2: second pass with key terms ---------------------------------------------
    if "stt2" in steps:
        t0 = time.perf_counter()
        glossary_now, glossary_hash = current_glossary()
        first = load_transcript(need(raw, "stt2"))
        inputs = {"audio": file_sha256(need(audio, "stt2")), "raw": file_sha256(raw),
                  "glossary": glossary_hash, "engine": settings.engine, "code": code_hash("stt2"),
                  "user_glossary": user_glossary_hash}
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
                    user_terms = [e.term for e in user_glossary.entries] if user_glossary else []
                    first_text = " ".join(seg.text for seg in first.segments)
                    transcript = make_assemblyai(glossary_prompt=True, user_terms=user_terms,
                                                 first_pass=first_text).transcribe(flac, glossary_now)
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
        inputs = {"input": file_sha256(need(recognized, "align")), "audio": file_sha256(need(audio, "align")),
                  "code": code_hash("align")}
        skipped = up_to_date(aligned, inputs)
        if not skipped:
            transcript = load_transcript(recognized)
            if transcript.meta.get("timestamps") == "model-estimated":
                _require_alignment()
                emit("step-start", "align", f"{len(transcript.segments)} segments")
                transcript = align_module.align(transcript, audio, align_module.Wav2Vec2Emitter())
            else:
                emit("step-start", "align", "times already measured; passing the transcript through")
            save_transcript(transcript, aligned)
            write_stamp(aligned, inputs)
        step_done("align", aligned, skipped, 0.0, t0)

    english = aligned if "align" in steps or (aligned.is_file() and not recognized.is_file()) else recognized
    previous_fixed: dict[str, Transcript] = {}  # sha256 -> the correction this run replaced

    if "fix" in steps:
        t0 = time.perf_counter()
        glossary_now, glossary_hash = current_glossary()
        inputs = {"input": file_sha256(need(english, "fix")), "glossary": glossary_hash,
                  "context": context_hash, "model": settings.correction_model,
                  "prompt": correct.PROMPT_VERSION, "code": code_hash("fix", correct.PROMPT_VERSION)}
        skipped = up_to_date(fixed, inputs)
        cost, tokens = 0.0, None
        if not skipped:
            english_transcript = load_transcript(english)
            if fixed.is_file():  # the correction this run replaces: its translation may still be reused
                try:
                    previous_fixed = {file_sha256(fixed): load_transcript(fixed)}
                except Exception:
                    logger.exception("the earlier correction cannot be read; the translation runs in full")
            earlier = reusable_answers(fixed, inputs, "correction", english_transcript,
                                       {inputs["input"]: english_transcript}, _correction_plan)
            done, done_edits = (earlier[0], earlier[1].meta["correction"]["edits"]) if earlier else ({}, [])
            emit("step-start", "fix", f"{correct.PROMPT_VERSION}, {settings.correction_model}"
                 + (", only the blocks that changed or failed before" if done else ""))
            answers, parts = _BlockAnswers(), _blocks_or_none(english_transcript)

            def corrected_block(n: int, total: int) -> None:
                block = parts[n - 1] if 0 < n <= len(parts) else None
                if done and block and not answers.pending and all(seg.id in done for seg in block.segments):
                    ids = {seg.id for seg in block.segments}
                    answers("reused", correct.CorrectionAnswer(  # its text, as if just accepted
                        segments=[correct.SegmentText(id=seg.id, text=done[seg.id])
                                  for seg in block.segments],
                        edits=[correct.Edit(**e) for e in done_edits if e.get("id") in ids]))
                block_done("fix", answers, parts, None, n, total)

            observing = structured_call.result_observer.set(answers)
            try:
                result = correct.correct(english_transcript, glossary_now, context,
                                         model=settings.correction_model,
                                         refusal_fallback=settings.refusal_fallback,
                                         progress=corrected_block,
                                         **({"done": done, "done_edits": done_edits} if done else {}))
            finally:
                structured_call.result_observer.reset(observing)
            save_transcript(result, fixed)
            write_result_stamp(fixed, inputs, result.meta["correction"]["failed_blocks"],
                               result.meta["correction"].get("reverted_segments"))
            cost = result.meta["correction"]["cost_usd"]
            tokens = _usage_numbers(result.meta["correction"].get("usage"))
        step_done("fix", fixed, skipped, cost, t0, tokens=tokens)

    if "translate" in steps:
        t0 = time.perf_counter()
        glossary_now, glossary_hash = current_glossary()
        source_file = fixed if fixed.is_file() else need(english, "translate")
        cost = 0.0
        version = settings.translation_version
        if version == translate.V3:  # term classes for translation only; glossary.md is not touched
            glossary_now, glossary_hash, cost = _classified_glossary(
                settings, work, lang, glossary_now, glossary_hash, user_glossary, user_glossary_hash, emit)
        inputs = {"input": file_sha256(source_file), "glossary": glossary_hash, "context": context_hash,
                  "lang": lang, "model": settings.translation_model, "prompt": version,
                  "units": units.VERSION, "code": code_hash("translate", version)}
        skipped = up_to_date(translated, inputs)
        tokens = None
        if not skipped:
            source_transcript = load_transcript(source_file)
            # an earlier translation: send only blocks whose request changed or that failed before
            # (never for v3, whose markers are rendered over the whole video)
            earlier = None if version == translate.V3 else reusable_answers(
                translated, inputs, "translation", source_transcript,
                {inputs["input"]: source_transcript, **previous_fixed}, _translation_plan)
            done = earlier[0] if earlier else None
            emit("step-start", "translate", f"{version}, {settings.translation_model}"
                 + (", only the blocks that changed or failed before" if done else ""))
            answers, parts = _BlockAnswers(), _blocks_or_none(source_transcript, sentence_units=True)
            sources = {seg.id: seg.text for part in parts for seg in part.segments}

            def translated_block(n: int, total: int) -> None:
                block = parts[n - 1] if 0 < n <= len(parts) else None
                if done and block and not answers.pending and all(s.id in done for s in block.segments):
                    answers("reused", translate.TranslationAnswer(segments=[  # its text, as if just accepted
                        translate.SegmentText(id=s.id, text=done[s.id]) for s in block.segments]))
                block_done("translate", answers, parts, sources, n, total)

            observing = structured_call.result_observer.set(answers)
            try:
                result = translate.translate(source_transcript, glossary_now, lang, context=context,
                                             model=settings.translation_model, version=version,
                                             refusal_fallback=settings.refusal_fallback,
                                             progress=translated_block, **({"done": done} if done else {}))
            finally:
                structured_call.result_observer.reset(observing)
            save_transcript(result, translated)
            write_result_stamp(translated, inputs, result.meta["translation"]["failed_blocks"],
                               result.meta["translation"].get("reverted_segments"))
            cost += result.meta["translation"]["cost_usd"]
            tokens = _usage_numbers(result.meta["translation"].get("usage"))
        step_done("translate", translated, skipped and not cost, cost, t0, tokens=tokens)

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


def _blocks_or_none(transcript: Transcript, sentence_units: bool = False) -> list:
    """
    The blocks the step will send (as correct.py and translate.py make them), for
    the block events' segment ids and source text; an empty list if that fails,
    so a display problem never stops the step.
    """
    try:
        return blocks.make_blocks(units.make_units(transcript) if sentence_units else transcript)
    except Exception:
        logger.exception("blocks for the progress display not prepared; the run goes on")
        return []


def _classified_glossary(settings: RunSettings, work: Path, lang: str, glossary: Glossary, glossary_hash: str,
                         user_glossary: Glossary | None, user_glossary_hash: str, emit
                         ) -> tuple[Glossary, str, float]:
    """
    translation-v3: glossary.v3.md, the run glossary with a policy for every term
    (term_policy.py; skipped when up to date). Returns it, its hash and the cost.
    """
    out = work / "glossary.v3.md"
    inputs = {"glossary": glossary_hash, "user_glossary": user_glossary_hash, "model": settings.helper_model,
              "lang": lang, "version": term_policy.VERSION}
    cost = 0.0
    if not up_to_date(out, inputs):
        emit("step-start", "translate", f"{term_policy.VERSION}, {settings.helper_model}: term classes")
        result = term_policy.classify(glossary, lang, user_glossary, model=settings.helper_model)
        languages = sorted({lang, *result.glossary.languages()})
        header = (f"<!-- translation glossary (translation-v3): glossary.md with a policy per term; "
                  f"{result.classified} classified by {result.model}, {result.kept} from the user glossary "
                  "or a policy column -->")
        out.write_text(header + NL + NL + to_markdown(result.glossary, languages), encoding="utf-8",
                       newline=NL)
        write_stamp(out, inputs)
        cost = result.cost_usd
    text = out.read_text(encoding="utf-8")
    return load_glossary(out), _text_hash(text), cost


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
        if to == "gemini+align" and "align" in settings.steps and not youtube.align_extra_installed():
            raise PipelineError(f"AssemblyAI failed ({reason[:120]}) and the fallback {to} cannot run: "
                                f"{youtube.ALIGN_UNAVAILABLE}; then rerun (finished steps are kept)") from e
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

