"""
Replay mode: a recorded job's event stream (`.jsonl`, written by JobRunner
with a `record_dir`) played back through the normal job and Server-Sent
Events path, with no pipeline, no network and no cost. For checking the page
and for screenshots: `talk-autopsy web --replay <file> [--speed N]`.

- The page works as usual. The estimate uses `ReplayCore` (no network): the
  recording's title and duration; a link or file name containing `long` gives
  a four-hour duration, so the over-limit confirmation can be seen.
- "Start" plays the recording, whatever the form says: each event at its
  recorded time divided by `speed` (0 = all at once). The result folder is a
  demo folder under a temporary output root: the subtitles rebuilt from the
  recorded text and, when ffmpeg is available, a test-pattern video of the
  talk's length, so the player has something to play.
- The time-estimate history is a throwaway file: replays never teach this
  machine's history anything.

A recording is JSON lines: a header (`{"type": "header", "format": 1,
"summary": <job summary>}`) and then the job's events as they were stored.
"""

import json
import logging
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from talk_autopsy import youtube
from talk_autopsy.time_estimate import MachineKey
from talk_autopsy.web import jobs
from talk_autopsy.web.jobs import BusyError, Core, Job, JobRequest, JobRunner
from talk_autopsy.web.transcript import TranscriptView

logger = logging.getLogger(__name__)

LONG_SECONDS = 4 * 3600.0
END_TYPES = ("finished", "failed")


class RecordingError(ValueError):
    """The file is not a recording this version can replay."""


@dataclass
class Recording:
    path: Path
    summary: dict
    events: list[dict]

    @property
    def title(self) -> str:
        return str((self.summary.get("preview") or {}).get("title") or self.path.stem)

    @property
    def duration_s(self) -> float | None:
        value = (self.summary.get("preview") or {}).get("duration_s")
        return float(value) if value else None

    @property
    def steps(self) -> list[str]:
        return list(self.summary.get("steps") or [])


def load(path: Path) -> Recording:
    """The recording at `path`; RecordingError if it is not one."""
    path = Path(path)
    try:
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RecordingError(f"{path.name} cannot be read as a recording: {e}") from None
    if not lines or lines[0].get("type") != "header" or lines[0].get("format") != 1:
        raise RecordingError(f"{path.name} has no recording header (format 1)")
    events = lines[1:]
    if not events or any("type" not in e or not isinstance(e.get("t", 0), int | float) for e in events):
        raise RecordingError(f"{path.name} has no events, or an event without a type or time")
    if events[-1]["type"] not in END_TYPES:
        raise RecordingError(f"{path.name} does not end with a finished or failed event")
    return Recording(path, lines[0].get("summary") or {}, events)


class ReplayCore(Core):
    """The estimate screen without network: metadata from the recording."""

    def __init__(self, recording: Recording, root: Path):
        self.recording = recording
        self.root = root

    def output_root(self) -> Path:
        return self.root / "output"

    def youtube_runtimes(self) -> list[str]:
        return []

    def _duration(self, name: str) -> float | None:
        return LONG_SECONDS if "long" in name.lower() else self.recording.duration_s

    def fetch(self, url: str, runtimes: list[str]) -> youtube.VideoInfo:
        return youtube.VideoInfo(id="replay", url=url, title=self.recording.title,
                                 duration=self._duration(url), upload_date="")

    def probe(self, path: Path) -> float:
        duration = self._duration(Path(path).name)
        if duration is None:
            raise RuntimeError("the recording has no duration")
        return duration

    def machine_key(self, engine: str) -> MachineKey:
        return MachineKey("replay", "replay", "cloud", "cpu", 1)

    def check_keys(self, settings) -> None:  # pragma: no cover - never called in replay
        raise RuntimeError("replay mode runs no pipeline")


class ReplayRunner(JobRunner):
    """A JobRunner whose start() plays the recording instead of running the core."""

    def __init__(self, recording: Recording, speed: float = 1.0, root: Path | None = None,
                 make_video: bool = True, sleep=time.sleep, **kwargs):
        self.owns_root = root is None  # a temporary folder of its own is removed by close()
        self.root = Path(root or tempfile.mkdtemp(prefix="talk-autopsy-replay-"))
        super().__init__(core=ReplayCore(recording, self.root), history_file=self.root / "time-history.json",
                         **kwargs)
        self.recording = recording
        self.speed = max(0.0, float(speed))
        self.make_video = make_video
        self.sleep = sleep

    def close(self) -> None:
        """Remove the temporary demo folder (when the server stops)."""
        if self.owns_root:
            shutil.rmtree(self.root, ignore_errors=True)

    def start(self, request: JobRequest) -> Job:
        if not self._busy.acquire(blocking=False):
            raise BusyError(self._active)
        try:
            preview = self._cached_preview(request) or self.preview(request)
            self._gate(request, preview)  # the same confirmation as a real start
            job = Job(id=uuid.uuid4().hex[:12], request=request, preview=preview,
                      steps=self.recording.steps or jobs.job_steps(request.mode, preview.formats),
                      started_wall=time.strftime("%Y-%m-%dT%H:%M:%S"), clock=self.clock)
            job.replay = True
            self._register(job)
            threading.Thread(target=self._play, args=(job,), name=f"replay-{job.id}", daemon=True).start()
            return job
        except BaseException:
            self._active = None
            self._busy.release()
            raise

    def _play(self, job: Job) -> None:
        final = self.recording.events[-1]
        try:
            started = time.monotonic()
            for event in self.recording.events[:-1]:
                if self.speed > 0:
                    wait = float(event.get("t", 0)) / self.speed - (time.monotonic() - started)
                    if wait > 0:
                        self.sleep(wait)
                if event["type"] == "finished":  # only the last event ends a replay
                    continue
                job.add_recorded(event)
            if self.speed > 0:
                wait = float(final.get("t", 0)) / self.speed - (time.monotonic() - started)
                if wait > 0:
                    self.sleep(wait)
            payload = {k: v for k, v in final.items() if k not in ("id", "type", "t")}
            if final["type"] == "finished":
                folder = self._demo_folder(job, payload)
                payload["folder"], payload["result"] = str(folder), folder.name
            state = final["type"]
        except Exception:
            logger.exception("replay %s failed", job.id)
            state, payload = "failed", {"reason": "The replay stopped (see the log).", "hint": "log",
                                        "step": None, "cost_usd": 0.0, "stt_cost_usd": 0.0}
        finally:
            self._active = None
            self._busy.release()
        job.end(state, state, **payload)

    # --- the demo result folder ---------------------------------------------------------

    def _demo_folder(self, job: Job, payload: dict) -> Path:
        """The recorded file names in a temporary folder: subtitles from the recorded text, a test video."""
        name = Path(str(payload.get("folder") or "replay")).name or "replay"
        folder = self.core.output_root() / name
        folder.mkdir(parents=True, exist_ok=True)
        lang = job.request.target_lang
        target, english = subtitle_texts(job.view)
        files = [f.get("name", "") for f in payload.get("files") or []]
        stem = next((f[:-4] for f in files if f.endswith(".srt") and not f.endswith(".en.srt")), "replay")
        (folder / f"{stem}.srt").write_text(target, encoding="utf-8")
        (folder / f"{stem}.en.srt").write_text(english, encoding="utf-8")
        if self.make_video:
            make_test_video(folder / f"{stem}.mp4", self.recording.duration_s or 60.0)
        marker = {"title": self.recording.title, "target_lang": lang, "replay": True}
        (folder / jobs.RESULT_MARKER).write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
        return folder


def _srt_time(seconds: float) -> str:
    ms = int(round(max(seconds, 0.0) * 1000))
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def subtitle_texts(view: TranscriptView) -> tuple[str, str]:
    """(target, English) SRT text from a replayed view: one cue per row, timed by the rows' starts."""
    window = view.window(0, view.summary()["segments"] or 1)
    rows = [r for r in window["rows"] if r["start"] is not None]
    target, english = [], []
    for k, row in enumerate(rows):
        start = float(row["start"])
        end = float(rows[k + 1]["start"]) if k + 1 < len(rows) else start + 4.0
        timing = f"{_srt_time(start)} --> {_srt_time(max(end - 0.1, start + 0.5))}"
        en = " ".join(p["t"] for s in row["segments"] for p in s["pieces"] if p["k"] != "del")
        tr = " ".join(u["text"] for u in row["units"] if u["text"]) or en
        english.append(f"{k + 1}\n{timing}\n{en}\n")
        target.append(f"{k + 1}\n{timing}\n{tr}\n")
    return "\n".join(target), "\n".join(english)


def make_test_video(path: Path, seconds: float) -> bool:
    """A small test-pattern MP4 with a tone, `seconds` long (at most 15 minutes); False without ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or path.is_file():
        return path.is_file()
    seconds = max(5.0, min(float(seconds), 900.0))
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=10:duration={seconds:.0f}",
               "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds:.0f}",
               "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
               "-movflags", "+faststart", str(path)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        logger.exception("test video not made")
        return False
    if result.returncode != 0:
        logger.warning("test video not made: %s", result.stderr.strip()[-300:])
        return False
    return True
