"""
Jobs started from the web page: one at a time, each in a worker thread, each
turning the core's progress callbacks into an append-only list of events.

    JobRequest (validated)  ->  JobRunner.preview()  ->  Preview (metadata, cost, time)
                            ->  JobRunner.start()    ->  cost gate -> Job + worker thread
    worker: [download] -> pipeline.run(progress) -> outputs.write_outputs -> finished | failed

- **One job at a time.** A second start while one runs raises BusyError.
- **Cost gate here, not in the page.** Above the per-video limit, or with no
  cost estimate at all, a job starts only if the request sends back that exact
  estimate (`confirmed_cost_usd`) or `confirmed_no_estimate`. The confirmed
  amount becomes the pipeline's own `max_cost_usd`, so the pipeline still stops
  if its estimate after speech recognition is higher.
- **Events** have increasing integer ids; readers ask for the events after an
  id (replay on reconnect). Text payloads are capped and the list is bounded.
  Every string is cleaned of secret values before it is stored.
- **Progress** comes only from what the core reports: positions for correction
  and translation blocks (and Gemini chunks), nothing invented for the rest.
  Text events are read from each step's output file once the step finished.
- **No cancel:** the core has no cooperative cancellation (work/STATUS.md).

The core is reached through `Core`; tests pass a fake with the same methods.
No FastAPI here.
"""

import logging
import math
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from talk_autopsy import config, ingest, outputs, pipeline, secret_registry, youtube
from talk_autopsy.config import ConfigurationError
from talk_autopsy.glossary import GlossaryError
from talk_autopsy.segments import Transcript, load_transcript
from talk_autopsy.stt.chunked import ProgressEvent
from talk_autopsy.time_estimate import NO_ESTIMATE, MachineKey, TimeEstimate, TimeEstimator
from talk_autopsy.web import settings as web_settings
from talk_autopsy.web.sources import (
    PLAYLIST_MESSAGE,
    InputError,
    check_media_path,
    is_audio_only,
    normalize_youtube_url,
)

logger = logging.getLogger(__name__)

MAX_EVENTS = 20_000  # one job; an hour of video gives a few thousand
MAX_TEXT_EVENTS_PER_KIND = 2_500
MAX_TEXT_CHARS = 400
MAX_REASON_CHARS = 300
KEEP_JOBS = 5  # finished jobs kept in memory for a page reload
PREVIEW_TTL_S = 3600.0
TICK_S = 30.0  # the remaining time is recalculated at least this often while a job runs
PIPELINE_STEPS = list(pipeline.STEPS)  # stt, glossary, stt2, align, fix, translate, srt
HIDDEN = "[hidden]"

FILE_PURPOSE = {"mp4-burned": "tv", "srt": "subtitles", "ass": "subtitles_styled",
                "bilingual": "bilingual", "mp4-soft": "video_tracks"}


# --- request ----------------------------------------------------------------------------


class JobRequest(BaseModel):
    """What the page sends to estimate or start a job; exactly one source matching `mode`."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["url", "file"]
    url: str | None = None
    path: str | None = None
    profile: Literal["tv", "laptop", "all"] = "tv"
    domain: Literal["none", "web3"] = "none"
    target_lang: str
    confirmed_cost_usd: float | None = None
    confirmed_no_estimate: bool = False

    @model_validator(mode="after")
    def _one_source(self) -> "JobRequest":
        url = (self.url or "").strip()
        path = (self.path or "").strip()
        if url and path:
            raise InputError("both_given", "Give either a link or a file, not both.")
        if self.mode == "url":
            if path:
                raise InputError("both_given", "The link mode takes a link, not a file.")
            self.url = normalize_youtube_url(url)
        else:
            if url:
                raise InputError("both_given", "The file mode takes a file, not a link.")
            self.path = str(check_media_path(path))
        if self.target_lang not in web_settings.target_codes():
            raise InputError("bad_language", f"Language {self.target_lang!r} is not offered.")
        if self.confirmed_cost_usd is not None and not math.isfinite(self.confirmed_cost_usd):
            raise InputError("bad_confirmation", "The cost confirmation is not a number.")
        return self

    @property
    def source(self) -> str:
        return self.url if self.mode == "url" else self.path  # type: ignore[return-value]


def input_error(exc: Exception) -> InputError | None:
    """The InputError inside a pydantic ValidationError (or the error itself), if there is one."""
    if isinstance(exc, InputError):
        return exc
    for error in getattr(exc, "errors", lambda: [])():
        inner = (error.get("ctx") or {}).get("error")
        if isinstance(inner, InputError):
            return inner
    return None


# --- the core, behind one object -------------------------------------------------------


class Core:
    """The real modules. Tests pass a fake with the same methods."""

    def output_root(self) -> Path:
        return config.output_dir()

    def youtube_runtimes(self) -> list[str]:
        return youtube.require_youtube_tools()

    def fetch(self, url: str, runtimes: list[str]) -> youtube.VideoInfo | youtube.PlaylistInfo:
        return youtube.fetch(url, runtimes)

    def download(self, video: youtube.VideoInfo, folder: Path, runtimes: list[str]) -> Path:
        return youtube.download(video, folder, runtimes).video

    def probe(self, path: Path) -> float:
        return ingest.probe(path)

    def user_glossary(self, domain: str, work_dir: Path) -> Path | None:
        if domain == "none":
            return None
        # Borrowed from the CLI until cli.py is split (v1.1 session 3).
        from talk_autopsy.cli import _run_glossary, _user_glossary_paths
        return _run_glossary(_user_glossary_paths(None, domain), work_dir)

    def check_keys(self, settings: pipeline.RunSettings) -> None:
        pipeline.check_keys(settings)

    def run_pipeline(self, settings: pipeline.RunSettings, progress) -> pipeline.RunReport:
        return pipeline.run(settings, progress=progress)

    def read_transcript(self, path: Path) -> Transcript | None:
        return load_transcript(path) if path.is_file() else None

    def write_outputs(self, settings: pipeline.RunSettings, formats: list[str], dest: Path, stem: str,
                      video: Path, progress) -> list[outputs.Written]:
        from talk_autopsy.glossary import load_glossary
        lang = settings.target_lang
        translated_path = settings.work_dir / f"{lang}.json"
        translated = (load_transcript(translated_path) if "bilingual" in formats and translated_path.is_file()
                      else None)
        glossary_file = settings.work_dir / "glossary.md"
        glossary = load_glossary(glossary_file) if glossary_file.is_file() else None
        return outputs.write_outputs(settings.work_dir, settings.video_id, lang, dest, stem, formats,
                                     video=video, glossary=glossary, translated=translated, progress=progress)

    def mark_done(self, folder: Path, files: list[str], cost: dict) -> None:
        youtube.mark_done(folder, files, cost)

    def machine_key(self, engine: str) -> MachineKey:
        model = "/".join((config.helper_model(), config.correction_model(), config.translation_model()))
        compute = "int8" if engine == "faster-whisper" else "cloud"
        return MachineKey(engine, model, compute, "cpu", os.cpu_count() or 0)


# --- preview (the estimate screen) -------------------------------------------------------


@dataclass
class Preview:
    mode: str
    source: str
    title: str
    duration_s: float | None
    cost: youtube.Estimate
    formats: list[str]
    time: TimeEstimate
    notes: list[str] = field(default_factory=list)  # codes: audio_only, quality_not_measured
    video: youtube.VideoInfo | None = None
    made: float = field(default_factory=time.monotonic)

    @property
    def cost_known(self) -> bool:
        return self.cost.minutes is not None

    @property
    def amount_usd(self) -> float | None:
        """The estimate the page must send back to confirm (Claude + speech recognition, 2 decimals)."""
        return round(self.cost.total_usd, 2) if self.cost_known else None

    def needs_confirmation(self, limit_usd: float) -> bool:
        return not self.cost_known or self.cost.total_usd > limit_usd

    def as_dict(self, limit_usd: float) -> dict:
        return {
            "mode": self.mode, "source": self.source, "title": self.title,
            "duration_s": None if self.duration_s is None else round(self.duration_s),
            "cost": None if not self.cost_known else {
                "claude_usd": self.cost.claude_usd, "stt_usd": self.cost.stt_usd,
                "total_usd": self.amount_usd},
            "limit_usd": limit_usd,
            "needs_confirmation": self.needs_confirmation(limit_usd),
            "time": self.time.as_dict(),
            "formats": self.formats,
            "notes": self.notes,
        }


def job_steps(mode: str, formats: list[str]) -> list[str]:
    """The steps the page lists, in order."""
    return (["download"] if mode == "url" else []) + PIPELINE_STEPS + ["outputs"]


def output_step(formats: list[str]) -> str:
    """The time-history name of the outputs step: burn-in, a subtitle-track MP4 or subtitle files take
    very different times, so each has its own rate."""
    if "mp4-burned" in formats:
        return "outputs_burn"
    return "outputs_soft" if "mp4-soft" in formats else "outputs_subs"


def estimator_steps(mode: str, formats: list[str]) -> list[str]:
    return (["download"] if mode == "url" else []) + PIPELINE_STEPS + [output_step(formats)]


# --- events and jobs -----------------------------------------------------------------------


def secret_values() -> list[str]:
    """The values of every registered secret that is set (8+ characters), longest first."""
    values = [os.environ.get(name, "").strip() for name in secret_registry.ALL]
    return sorted({v for v in values if len(v) >= 8}, key=len, reverse=True)


def scrub(value, secrets: list[str]):
    """`value` with every secret value replaced by [hidden], in nested dicts and lists too."""
    if isinstance(value, str):
        for secret in secrets:
            if secret in value:
                value = value.replace(secret, HIDDEN)
        return value
    if isinstance(value, dict):
        return {k: scrub(v, secrets) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [scrub(v, secrets) for v in value]
    return value


def short(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class BusyError(RuntimeError):
    def __init__(self, active_id: str | None):
        super().__init__("Another video is being processed; wait until it finishes.")
        self.active_id = active_id


class GateError(RuntimeError):
    """The cost gate refused the start; `code`: confirm_cost, cost_mismatch, confirm_no_estimate."""

    def __init__(self, code: str, message: str, amount_usd: float | None = None):
        super().__init__(message)
        self.code = code
        self.amount_usd = amount_usd


@dataclass
class Job:
    id: str
    request: JobRequest
    preview: Preview
    steps: list[str]
    started_wall: str
    clock: object = time.monotonic
    state: str = "running"  # running | finished | failed
    cost_usd: float = 0.0
    stt_cost_usd: float = 0.0
    _events: list[dict] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _next_id: int = 1
    _text_counts: dict[str, int] = field(default_factory=dict)
    _truncated: bool = False
    _start: float = 0.0

    def __post_init__(self) -> None:
        self._start = self.clock()
        self._secrets = secret_values()

    def add(self, type_: str, **payload) -> dict | None:
        """Append an event; returns it, or None if it was dropped by the caps."""
        with self._lock:
            if type_ == "text":
                kind = payload.get("kind", "")
                count = self._text_counts.get(kind, 0)
                if count >= MAX_TEXT_EVENTS_PER_KIND or len(self._events) >= MAX_EVENTS - 100:
                    if not self._truncated:
                        self._truncated = True
                        self._append("notice", {"code": "text_truncated",
                                                "text": "Only the first part of the text is shown."})
                    return None
                self._text_counts[kind] = count + 1
            elif len(self._events) >= MAX_EVENTS and type_ not in ("finished", "failed"):
                return None
            return self._append(type_, payload)

    def _append(self, type_: str, payload: dict) -> dict:
        event = {**scrub(payload, self._secrets), "id": self._next_id, "type": type_,
                 "t": round(self.clock() - self._start, 1)}
        self._next_id += 1
        self._events.append(event)
        return event

    def events_after(self, after: int = 0) -> tuple[list[dict], bool]:
        """Events with id > `after`, and whether the job has ended (its last event is in the list)."""
        with self._lock:
            index = max(0, min(after, len(self._events)))  # ids are 1..n, so id k is at index k-1
            return list(self._events[index:]), self.state != "running"

    @property
    def last_event_id(self) -> int:
        with self._lock:
            return self._next_id - 1

    def end(self, state: str, type_: str, **payload) -> None:
        """The last event and the end state together, so a reader sees both at once."""
        with self._lock:
            self._append(type_, payload)
            self.state = state

    def summary(self, limit_usd: float) -> dict:
        return {"id": self.id, "state": self.state, "started": self.started_wall, "steps": self.steps,
                "request": {"mode": self.request.mode, "source": self.request.source,
                            "profile": self.request.profile, "domain": self.request.domain,
                            "target_lang": self.request.target_lang},
                "preview": self.preview.as_dict(limit_usd), "cost_usd": round(self.cost_usd, 4),
                "stt_cost_usd": round(self.stt_cost_usd, 4), "last_event_id": self.last_event_id}


# --- the progress adapter ------------------------------------------------------------------

_BLOCK = re.compile(r"block (\d+)/(\d+)")
_CLAUDE_USD = re.compile(r", (\d+\.\d+) USD")
_STT_USD = re.compile(r"speech recognition (\d+\.\d+) USD")
_ESTIMATE_USD = re.compile(r"about (\d+\.\d+) USD$")


def step_costs(detail: str) -> tuple[float, float]:
    """(Claude USD, speech recognition USD) from a pipeline step-done line (its text before '; ')."""
    head = detail.split("; ")[0]
    claude, stt = _CLAUDE_USD.search(head), _STT_USD.search(head)
    return (float(claude.group(1)) if claude else 0.0, float(stt.group(1)) if stt else 0.0)


class _Progress:
    """Core ProgressEvents -> job events, for one job. Any error here becomes a notice, not a crash."""

    def __init__(self, runner: "JobRunner", job: Job, settings: pipeline.RunSettings,
                 estimator: TimeEstimator):
        self.runner, self.job, self.settings, self.estimator = runner, job, settings, estimator
        self.open_step: str | None = None
        self.last_step: str | None = None  # the step started or finished last
        self.started_at: dict[str, float] = {}
        self.last_mark = job.clock()
        self.claude_estimate: dict[str, float] = {}
        self.display_error_reported = False

    def __call__(self, event: ProgressEvent) -> None:
        try:
            self._handle(event)
        except Exception as e:  # a display problem must not stop a paid run
            logger.exception("job %s: progress event not shown", self.job.id)
            if not self.display_error_reported:
                self.display_error_reported = True
                self.job.add("notice", code="progress_display_error",
                             text=f"A progress update could not be shown ({type(e).__name__}); "
                                  "the run goes on.")

    def _start(self, step: str, detail: str = "") -> None:
        if step not in self.started_at:
            self.started_at[step] = self.job.clock()
            self.estimator.step_started(step)
        self.open_step = self.last_step = step
        self.job.add("step_started", step=step, detail=short(detail, 200))

    def _handle(self, ev: ProgressEvent) -> None:
        job = self.job
        if ev.engine != "pipeline":  # the chunked Gemini engine (a fallback) reports chunks
            if ev.event in ("chunk-done", "chunk-cached") and ev.chunks:
                job.add("step_progress", step="stt", position=ev.chunk, total=ev.chunks, unit="chunk")
                self.estimator.step_progress("stt", ev.chunk, ev.chunks)
            elif ev.event == "fallback":
                job.add("notice", code="fallback", text=short(ev.detail, 300))
            return
        step = "stt" if ev.stage == "audio" else ev.stage
        if ev.event == "step-start":
            self._start(step, ("audio: " if ev.stage == "audio" else "") + ev.detail)
        elif ev.event in ("step-done", "step-skipped"):
            self._finished(step, ev.event == "step-skipped", ev.detail)
        elif ev.event == "block-done":
            match = _BLOCK.search(ev.detail)
            if match:
                position, total = int(match.group(1)), int(match.group(2))
                job.add("step_progress", step=step, position=position, total=total, unit="block")
                self.estimator.step_progress(step, position, total)
        elif ev.event == "estimate":
            match = _ESTIMATE_USD.search(ev.detail)
            if match:
                self.claude_estimate[step] = self.claude_estimate.get(step, 0.0) + float(match.group(1))
                job.add("estimate_updated", claude_usd=round(sum(self.claude_estimate.values()), 4),
                        claude_parts={k: round(v, 4) for k, v in self.claude_estimate.items()})
        elif ev.event in ("fallback", "srt"):
            job.add("notice", code=ev.event, text=short(ev.detail, 300))

    def _finished(self, step: str, skipped: bool, detail: str) -> None:
        job, now = self.job, self.job.clock()
        seconds = now - self.started_at.get(step, self.last_mark)
        self.last_mark = now
        claude, stt = (0.0, 0.0) if skipped else step_costs(detail)
        job.cost_usd += claude
        job.stt_cost_usd += stt
        tokens = None if skipped else self._tokens(step)
        if step in self.started_at:
            self.estimator.step_finished(step, None if not skipped else seconds, skipped)
        else:  # passed through or skipped without a start event: not a measurement
            self.estimator.step_finished(step, None, skipped)
        self.open_step, self.last_step = None, step
        job.add("step_finished", step=step, skipped=skipped, seconds=round(max(seconds, 0.0), 1),
                cost_usd=round(claude, 4), stt_cost_usd=round(stt, 4), tokens=tokens,
                detail=short(detail.split("; ", 1)[1] if "; " in detail else "", 200),
                total_cost_usd=round(job.cost_usd, 4), total_stt_cost_usd=round(job.stt_cost_usd, 4))
        self._text(step)
        self.runner._time_update(job, self.estimator, force=True)

    def _tokens(self, step: str) -> dict | None:
        files = {"fix": ("fixed.json", "correction"), "translate": (f"{self.settings.target_lang}.json",
                                                                   "translation")}
        if step not in files:
            return None
        try:
            name, key = files[step]
            transcript = self.runner.core.read_transcript(self.settings.work_dir / name)
            usage = (transcript.meta.get(key) or {}).get("usage") if transcript else None
            if not usage:
                return None
            return {"input": int(usage.get("input_tokens", 0)), "output": int(usage.get("output_tokens", 0))}
        except Exception:
            logger.exception("job %s: tokens of %s not read", self.job.id, step)
            return None

    def _text(self, step: str) -> None:
        """Text events from the finished step's output file (not live: the core has no text callback)."""
        work, core, job = self.settings.work_dir, self.runner.core, self.job
        try:
            if step == "stt":
                transcript = core.read_transcript(work / "raw.json")
                for s in transcript.segments if transcript else []:
                    if job.add("text", kind="recognized", segment=s.id, start=round(s.start, 2),
                               text=short(s.text)) is None:
                        break
            elif step == "fix":
                transcript = core.read_transcript(work / "fixed.json")
                edits = ((transcript.meta.get("correction") or {}).get("edits") or []) if transcript else []
                for edit in edits:
                    added = job.add("text", kind="edit", segment=edit.get("id"),
                                    before=short(edit.get("before", "")), text=short(edit.get("after", "")))
                    if added is None:
                        break
            elif step == "translate":
                transcript = core.read_transcript(work / f"{self.settings.target_lang}.json")
                for s in transcript.segments if transcript else []:
                    if job.add("text", kind="translated", segment=s.id, start=round(s.start, 2),
                               source_ids=s.source_ids, text=short(s.text)) is None:
                        break
        except Exception:
            logger.exception("job %s: text of step %s not read", job.id, step)


# --- failure messages ------------------------------------------------------------------------


def failure(exc: BaseException) -> tuple[str, str]:
    """(reason, hint code) in plain words; never a stack trace."""
    known = (pipeline.PipelineError, ConfigurationError, youtube.YouTubeError, outputs.OutputError,
             GlossaryError, FileNotFoundError, InputError)
    if isinstance(exc, known):
        reason = short(str(exc).splitlines()[0] if str(exc) else type(exc).__name__, MAX_REASON_CHARS)
    else:
        reason = f"Unexpected error ({type(exc).__name__}); the details are in the log file."
    if isinstance(exc, ConfigurationError):
        hint = "doctor"
    elif isinstance(exc, youtube.YouTubeError):
        hint = "youtube"
    elif isinstance(exc, outputs.OutputError):
        hint = "outputs"
    elif isinstance(exc, pipeline.PipelineError) and "above the limit" in str(exc):
        hint = "cost_limit"
    elif isinstance(exc, known):
        hint = "input"
    else:
        hint = "log"
    return reason, hint


# --- the runner ------------------------------------------------------------------------------


class JobRunner:
    def __init__(self, core: Core | None = None, history_file: Path | None = None,
                 limit_usd: float = web_settings.COST_LIMIT_USD, clock=time.monotonic, tick_s: float = TICK_S,
                 engine: str = pipeline.DEFAULT_ENGINE):
        self.core = core or Core()
        self.history_file = history_file
        self.limit_usd = limit_usd
        self.clock = clock
        self.tick_s = tick_s
        self.engine = engine
        self._busy = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._active: str | None = None
        self._last: str | None = None
        self._previews: dict[tuple[str, str, str], Preview] = {}
        self._previews_lock = threading.Lock()
        self._time_sent: dict[str, float | None] = {}

    # --- preview ---------------------------------------------------------------------

    def _estimator(self, mode: str, formats: list[str], duration: float | None) -> TimeEstimator:
        try:
            key = self.core.machine_key(self.engine)
        except Exception:
            key = None
        return TimeEstimator(key, duration, estimator_steps(mode, formats), history_file=self.history_file)

    def preview(self, request: JobRequest) -> Preview:
        """Metadata, cost and time for the estimate screen. InputError / YouTubeError / RuntimeError."""
        formats = list(web_settings.PROFILES[request.profile])
        notes = [] if request.target_lang in web_settings.MEASURED_LANGUAGES else ["quality_not_measured"]
        video = None
        if request.mode == "url":
            runtimes = self.core.youtube_runtimes()
            info = self.core.fetch(request.url, runtimes)
            if isinstance(info, youtube.PlaylistInfo):
                raise InputError("playlist", PLAYLIST_MESSAGE)
            video, title, duration = info, info.title, info.duration
        else:
            path = Path(request.path)
            title = path.name
            try:
                duration = self.core.probe(path)
            except (RuntimeError, OSError) as e:
                message = f"The file could not be read as media: {short(str(e), 200)}"
                raise InputError("unreadable_file", message) from None
            if is_audio_only(path):
                formats = [f for f in formats if f not in web_settings.VIDEO_FORMATS]
                notes.append("audio_only")
                if not formats:
                    formats = ["srt"]
        if duration is not None and (not math.isfinite(duration) or duration <= 0):
            duration = None
        try:
            time_estimate = self._estimator(request.mode, formats, duration).initial()
        except Exception:
            time_estimate = NO_ESTIMATE
        preview = Preview(request.mode, request.source, title, duration, youtube.estimate(duration), formats,
                          time_estimate, notes, video)
        with self._previews_lock:
            now = time.monotonic()
            self._previews = {k: p for k, p in self._previews.items() if now - p.made < PREVIEW_TTL_S}
            self._previews[(request.mode, request.source, request.profile)] = preview
        return preview

    def _cached_preview(self, request: JobRequest) -> Preview | None:
        with self._previews_lock:
            preview = self._previews.get((request.mode, request.source, request.profile))
        if preview is not None and time.monotonic() - preview.made < PREVIEW_TTL_S:
            return preview
        return None

    # --- start -----------------------------------------------------------------------

    def start(self, request: JobRequest) -> Job:
        """Start a job after the cost gate. BusyError, GateError, InputError, YouTubeError."""
        if not self._busy.acquire(blocking=False):
            raise BusyError(self._active)
        try:
            preview = self._cached_preview(request) or self.preview(request)
            max_cost = self._gate(request, preview)
            job = Job(id=uuid.uuid4().hex[:12], request=request, preview=preview,
                      steps=job_steps(request.mode, preview.formats),
                      started_wall=time.strftime("%Y-%m-%dT%H:%M:%S"), clock=self.clock)
            self._jobs[job.id] = job
            for old in [k for k in self._jobs if k != job.id][:-KEEP_JOBS]:
                self._jobs.pop(old, None)
            self._active = self._last = job.id
            threading.Thread(target=self._execute, args=(job, max_cost), name=f"job-{job.id}",
                             daemon=True).start()
            return job
        except BaseException:
            self._active = None
            self._busy.release()
            raise

    def _gate(self, request: JobRequest, preview: Preview) -> float:
        """The pipeline's max_cost_usd for this job, or GateError."""
        if not preview.cost_known:
            if not request.confirmed_no_estimate:
                raise GateError("confirm_no_estimate", "There is no cost estimate for this video; "
                                                       "confirm that you want to start anyway.")
            return self.limit_usd
        amount = preview.amount_usd
        if preview.cost.total_usd <= self.limit_usd:
            return self.limit_usd
        if request.confirmed_cost_usd is None:
            raise GateError("confirm_cost", f"The estimate {amount:.2f} USD is above the limit of "
                                            f"{self.limit_usd:.2f} USD; confirm this amount to start.",
                            amount)
        if abs(request.confirmed_cost_usd - amount) > 0.005:
            raise GateError("cost_mismatch", f"The confirmed amount {request.confirmed_cost_usd:.2f} USD "
                                             f"is not the estimate {amount:.2f} USD; estimate again.", amount)
        return max(self.limit_usd, amount)

    # --- reading ---------------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def current(self) -> Job | None:
        """The running job, else the last one."""
        return self._jobs.get(self._active or self._last or "")

    @property
    def active_id(self) -> str | None:
        return self._active

    # --- the worker ------------------------------------------------------------------

    def _time_update(self, job: Job, estimator: TimeEstimator, force: bool = False) -> None:
        estimate = estimator.remaining()
        previous = self._time_sent.get(job.id, -1.0)
        seconds = estimate.seconds
        if not force and previous is not None and seconds is not None and previous >= 0:
            if abs(seconds - previous) < max(15.0, 0.1 * previous):
                return
        if not force and seconds is None and previous is None:
            return
        self._time_sent[job.id] = seconds
        job.add("estimate_updated", time=estimate.as_dict())

    def _ticker(self, job: Job, estimator: TimeEstimator, stop: threading.Event) -> None:
        while not stop.wait(self.tick_s):
            try:
                self._time_update(job, estimator)
            except Exception:
                logger.exception("job %s: time update failed", job.id)

    def _execute(self, job: Job, max_cost: float) -> None:
        request, preview, core = job.request, job.preview, self.core
        estimator = self._estimator(request.mode, preview.formats, preview.duration_s)
        stop = threading.Event()
        threading.Thread(target=self._ticker, args=(job, estimator, stop), name=f"tick-{job.id}",
                         daemon=True).start()
        last_step, progress = None, None
        final: tuple[str, dict] = ("failed", {"reason": "Interrupted.", "hint": "log", "step": None,
                                              "cost_usd": 0.0, "stt_cost_usd": 0.0})
        try:
            logger.info("job %s started: %s %s, profile %s, %s", job.id, request.mode, request.source,
                        request.profile, request.target_lang)
            job.add("estimate_updated", time=estimator.initial().as_dict(),
                    cost=preview.as_dict(self.limit_usd)["cost"])
            output_root = core.output_root()
            if request.mode == "url":
                video = preview.video
                name, context = f"yt-{youtube.safe_name(video.id, 40)}", video.context()
                dest, stem = output_root / youtube.video_folder_name(video), youtube.video_file_stem(video)
                source = Path(".")  # replaced after the download
            else:
                source = Path(request.path)
                name, context = "", ""
                stem = youtube.safe_name(source.stem)
                dest = output_root / stem
            settings = pipeline.RunSettings(source=source, target_lang=request.target_lang,
                                            steps=list(PIPELINE_STEPS), engine=self.engine,
                                            max_cost_usd=max_cost, context=context, name=name)
            core.check_keys(settings)  # fail before any download or upload
            if request.mode == "url":
                last_step = "download"
                t0 = job.clock()
                job.add("step_started", step="download", detail="")
                estimator.step_started("download")
                reused = (dest / f"{stem}.mp4").is_file()  # youtube.download keeps an MP4 already there
                settings.source = core.download(preview.video, dest, core.youtube_runtimes())
                estimator.step_finished("download", skipped=reused)
                job.add("step_finished", step="download", skipped=reused, seconds=round(job.clock() - t0, 1),
                        cost_usd=0.0, stt_cost_usd=0.0, tokens=None, detail=settings.source.name,
                        total_cost_usd=0.0, total_stt_cost_usd=0.0)
            settings.glossary = core.user_glossary(request.domain, settings.work_dir)
            progress = _Progress(self, job, settings, estimator)
            last_step = "pipeline"
            report = core.run_pipeline(settings, progress)
            last_step = "outputs"
            out_step = output_step(preview.formats)
            t0 = job.clock()
            job.add("step_started", step="outputs", detail=", ".join(preview.formats))
            estimator.step_started(out_step)
            written = core.write_outputs(settings, preview.formats, dest, stem, settings.source,
                                         lambda message: job.add("notice", code="outputs",
                                                                 text=short(message, 300)))
            estimator.step_finished(out_step)
            job.add("step_finished", step="outputs", skipped=False, seconds=round(job.clock() - t0, 1),
                    cost_usd=0.0, stt_cost_usd=0.0, tokens=None, detail="",
                    total_cost_usd=round(job.cost_usd, 4), total_stt_cost_usd=round(job.stt_cost_usd, 4))
            # the report's totals are the pipeline's own numbers; they win over the parsed ones
            job.cost_usd, job.stt_cost_usd = report.cost_usd, report.stt_cost_usd
            files = [w.path.name for w in written]
            if request.mode == "url":
                core.mark_done(dest, files, {"claude_usd": report.cost_usd, "stt_usd": report.stt_cost_usd})
            if not report.fallbacks:  # a fallback engine's times would mislead this machine's history
                estimator.record()
            final = ("finished", {
                "folder": str(dest),
                "files": [{"name": w.path.name, "format": w.format, "purpose": FILE_PURPOSE.get(w.format, "")}
                          for w in written],
                "cost_usd": round(report.cost_usd, 4), "stt_cost_usd": round(report.stt_cost_usd, 4),
                "fallbacks": [short(f, 200) for f in report.fallbacks]})
            logger.info("job %s finished: %.4f USD Claude, %.4f USD speech recognition", job.id,
                        report.cost_usd, report.stt_cost_usd)
        except BaseException as e:
            if isinstance(e, Exception):
                logger.exception("job %s failed at %s", job.id, last_step)
            reason, hint = failure(e)
            if last_step == "pipeline" and progress is not None:
                last_step = progress.open_step or progress.last_step
            final = ("failed", {"reason": reason, "hint": hint, "step": last_step,
                                "cost_usd": round(job.cost_usd, 4),
                                "stt_cost_usd": round(job.stt_cost_usd, 4)})
            if not isinstance(e, Exception):
                raise
        finally:
            # The lock is free before the last event, so the page can start the next video as soon
            # as it sees this one end.
            stop.set()
            self._time_sent.pop(job.id, None)
            self._active = None
            self._busy.release()
            job.end(final[0], final[0], **final[1])
