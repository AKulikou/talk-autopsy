"""
Courses: a YouTube playlist processed lesson by lesson from the web page (v1.2, docs/web.md).

    CourseRequest -> CourseRunner.preview() -> CoursePreview (lessons, "already done", estimate)
                  -> CourseRunner.start()   -> cost gate on the selected lessons' total -> worker thread
    worker: for each selected lesson, in playlist order:
              full metadata -> JobRunner.preview(folder=<lesson folder>) -> JobRunner.run_lesson()
              -> the lesson's status -> the course glossary (web/course_glossary.py) -> course.json

- **One lock.** The course holds the JobRunner's one-job lock from the first
  lesson to the last, so nothing else starts in between; each lesson is an
  ordinary job (same events, same waiting screen, same result files).
- **Cost gate here.** The total of the selected lessons (counting the cache)
  above the per-video limit needs the page to send back that exact amount;
  each lesson then gets the pipeline's usual ceiling (`JobRunner.allowance`).
- **A failed lesson does not stop the course:** it is marked with the reason
  (the job's own failure payload) and the next lesson starts.
- **Stop after the current lesson:** the flag is read between lessons; the
  rest stay queued.
- **Folders:** `<output>/<date> <course title>/NN <lesson title>/` (NN = the
  playlist position, zero-padded). The course's hidden service folder holds
  `course.json`: the request, every lesson's folder and status, the state.
  Re-submitting the same playlist finds the folder by the playlist id and
  reuses the lesson folders, so finished lessons come from the cache.
- **Restart safety:** a course whose `course.json` still says `running` (the
  server was closed mid-course) or `stopped` with lessons left is listed as
  unfinished; "Continue" estimates the rest again.

No FastAPI here; the core is reached through the JobRunner's `Core`.
"""

import datetime as dt
import json
import logging
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from talk_autopsy import config, youtube
from talk_autopsy.web import course_glossary
from talk_autopsy.web import settings as web_settings
from talk_autopsy.web.jobs import BusyError, GateError, JobRequest, JobRunner, failure, short
from talk_autopsy.web.sources import InputError, normalize_playlist_url

logger = logging.getLogger(__name__)
console = logging.getLogger("talk_autopsy.console")

MAX_LESSONS = 100
COURSE_MARKER = "course.json"
PREVIEW_TTL_S = 3600.0
MAX_SCAN = 300
UNAVAILABLE = {"private": "private", "needs_auth": "private", "subscriber_only": "private",
               "premium_only": "private"}
UNAVAILABLE_TITLES = {"[private video]": "private", "[deleted video]": "deleted", "[unavailable video]":
                      "unavailable"}
ERROR_KEYS = ("reason", "hint", "service", "kind", "status", "step")
STATUSES = ("not_selected", "queued", "running", "finished", "failed", "skipped")


class CourseRequest(BaseModel):
    """What the page sends to estimate or start a course."""

    model_config = ConfigDict(extra="forbid")

    url: str
    profile: Literal["tv", "laptop", "all"] = "tv"
    domain: Literal["none", "web3"] = "none"
    target_lang: str
    course_glossary: bool = course_glossary.DEFAULT_ON
    selected: list[int] | None = None  # playlist positions; None: every available lesson
    confirmed_cost_usd: float | None = None
    confirmed_no_estimate: bool = False

    @model_validator(mode="after")
    def _check(self) -> "CourseRequest":
        self.url = normalize_playlist_url(self.url)
        if self.target_lang not in web_settings.target_codes():
            raise InputError("bad_language", f"Language {self.target_lang!r} is not offered.")
        if self.confirmed_cost_usd is not None and not math.isfinite(self.confirmed_cost_usd):
            raise InputError("bad_confirmation", "The cost confirmation is not a number.")
        if self.selected is not None:
            if len(self.selected) > MAX_LESSONS or any(not 1 <= p <= 10_000 for p in self.selected):
                raise InputError("bad_selection", "The lesson selection is not valid.")
            self.selected = sorted(set(self.selected))
        return self

    def key(self) -> tuple:
        return (self.url, self.profile, self.domain, self.target_lang, self.course_glossary)


@dataclass
class Lesson:
    position: int
    video: youtube.VideoInfo
    folder_name: str
    reason: str = ""  # why it cannot run: private, deleted, unavailable, no_duration
    ready: bool = False  # finished before (its folder's done marker) or fully in the cache
    claude_usd: float = 0.0
    stt_usd: float = 0.0
    time_s: float | None = None
    cost_known: bool = True
    status: str = "not_selected"
    job_id: str | None = None
    error: dict | None = None
    cost_usd: float = 0.0
    stt_cost_usd: float = 0.0
    result: str | None = None

    @property
    def available(self) -> bool:
        return not self.reason

    def as_dict(self) -> dict:
        return {"position": self.position, "id": self.video.id, "title": self.video.title,
                "duration_s": None if self.video.duration is None else round(self.video.duration),
                "available": self.available, "reason": self.reason, "ready": self.ready,
                "estimate": {"claude_usd": round(self.claude_usd, 4), "stt_usd": round(self.stt_usd, 4),
                             "known": self.cost_known, "time_s": None if self.time_s is None
                             else round(self.time_s)},
                "status": self.status, "job_id": self.job_id, "error": self.error,
                "cost_usd": round(self.cost_usd, 4), "stt_cost_usd": round(self.stt_cost_usd, 4),
                "result": self.result, "folder": self.folder_name}


@dataclass
class CoursePreview:
    request: CourseRequest
    playlist_id: str
    title: str
    folder: Path  # the course folder (made at the start)
    lessons: list[Lesson]
    more: int = 0  # playlist entries after the first MAX_LESSONS, not shown
    made: float = field(default_factory=time.monotonic)

    def chosen(self, selected: list[int] | None) -> list[Lesson]:
        """The lessons that will run: the selected ones that can (all available ones by default)."""
        wanted = None if selected is None else set(selected)
        return [lesson for lesson in self.lessons if lesson.available
                and (wanted is None or lesson.position in wanted)]

    def totals(self, selected: list[int] | None) -> dict:
        chosen = self.chosen(selected)
        claude = sum(lesson.claude_usd for lesson in chosen)
        stt = sum(lesson.stt_usd for lesson in chosen)
        times = [lesson.time_s for lesson in chosen]
        return {"count": len(chosen), "claude_usd": round(claude, 4), "stt_usd": round(stt, 4),
                "total_usd": round(claude + stt, 2), "known": all(lesson.cost_known for lesson in chosen),
                "time_s": None if not chosen or any(t is None for t in times) else round(sum(times)),
                "ready": sum(lesson.ready for lesson in chosen)}

    def needs_confirmation(self, selected: list[int] | None, limit_usd: float) -> bool:
        totals = self.totals(selected)
        return not totals["known"] or totals["total_usd"] > limit_usd

    def as_dict(self, selected: list[int] | None, limit_usd: float) -> dict:
        return {"title": self.title, "playlist_id": self.playlist_id, "url": self.request.url,
                "folder": str(self.folder), "folder_exists": self.folder.is_dir(),
                "lessons": [lesson.as_dict() for lesson in self.lessons], "more": self.more,
                "selected": [lesson.position for lesson in self.chosen(selected)],
                "totals": self.totals(selected), "limit_usd": limit_usd,
                "needs_confirmation": self.needs_confirmation(selected, limit_usd),
                "formats": list(web_settings.PROFILES[self.request.profile]),
                "course_glossary": self.request.course_glossary,
                "notes": [] if self.request.target_lang in web_settings.MEASURED_LANGUAGES
                else ["quality_not_measured"]}


def unavailable_reason(video: youtube.VideoInfo) -> str:
    reason = UNAVAILABLE.get(video.availability) or UNAVAILABLE_TITLES.get(video.title.strip().lower(), "")
    if not reason and not video.duration:
        reason = "no_duration"  # cannot be estimated (and often not playable: an upcoming stream)
    return reason


def lesson_folder_name(position: int, title: str, count: int) -> str:
    """`NN <title>`, cut at a word; NN zero-padded to the playlist's size, so the folders sort."""
    width = max(2, len(str(count)))
    return youtube.safe_name(f"{position:0{width}d} {title}", words=True)


def read_marker(folder: Path) -> dict | None:
    path = youtube.service_file(folder, COURSE_MARKER)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def find_course_folder(root: Path, playlist: str) -> Path | None:
    """The folder of an earlier run of this playlist (its course.json), newest first; None."""
    if not root.is_dir() or not playlist:
        return None
    try:
        folders = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime,
                         reverse=True)
    except OSError:
        return None
    for folder in folders[:MAX_SCAN]:
        marker = read_marker(folder)
        if marker and marker.get("playlist_id") == playlist:
            return folder
    return None


def unfinished(root: Path, running_folder: Path | None = None) -> list[dict]:
    """Courses left `running` (the server was closed) or `stopped` with lessons left; newest first."""
    found = []
    if not Path(root).is_dir():
        return found
    try:
        folders = sorted((p for p in Path(root).iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime,
                         reverse=True)
    except OSError:
        return found
    for folder in folders[:MAX_SCAN]:
        marker = read_marker(folder)
        if not marker or (running_folder is not None and folder == running_folder):
            continue
        lessons = [x for x in marker.get("lessons") or [] if isinstance(x, dict)]
        left = [x for x in lessons if x.get("status") in ("queued", "running")]
        if marker.get("state") in ("running", "stopped") and left:
            found.append({"folder": str(folder), "title": marker.get("title") or folder.name,
                          "url": marker.get("url"), "profile": marker.get("profile"),
                          "domain": marker.get("domain"), "target_lang": marker.get("target_lang"),
                          "course_glossary": bool(marker.get("course_glossary")),
                          "state": marker.get("state"), "updated": marker.get("updated"),
                          "left": [x.get("position") for x in left],
                          "done": sum(1 for x in lessons if x.get("status") == "finished"),
                          "count": sum(1 for x in lessons if x.get("status") != "not_selected")})
    return found


@dataclass
class Course:
    id: str
    preview: CoursePreview
    started_wall: str
    state: str = "running"  # running | finished | stopped
    stop_requested: bool = False
    current: int | None = None  # playlist position of the lesson running now
    current_job: object = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def folder(self) -> Path:
        return self.preview.folder

    @property
    def lessons(self) -> list[Lesson]:
        return self.preview.lessons

    def queue(self) -> list[Lesson]:
        return [lesson for lesson in self.lessons if lesson.status == "queued"]

    def summary(self, limit_usd: float) -> dict:
        r = self.preview.request
        active = [x for x in self.lessons if x.status != "not_selected"]
        done = [x for x in active if x.status in ("finished", "failed", "skipped")]
        left = [x for x in active if x.status == "queued"]
        job = self.current_job
        running_left = getattr(job, "time_left", None) if job is not None and self.current else None
        times = [x.time_s for x in left]
        time_left = None if any(t is None for t in times) else sum(times) + (running_left or 0.0)
        return {"id": self.id, "state": self.state, "title": self.preview.title, "folder": str(self.folder),
                "folder_id": self.folder.name, "started": self.started_wall,
                "stop_requested": self.stop_requested,
                "current": self.current, "current_job": getattr(job, "id", None) if self.current else None,
                "count": len(active), "done": len(done),
                "finished": sum(1 for x in active if x.status == "finished"),
                "failed": sum(1 for x in active if x.status == "failed"),
                "cost_usd": round(sum(x.cost_usd for x in active)
                                  + (getattr(job, "cost_usd", 0.0) if self.current else 0.0), 4),
                "stt_cost_usd": round(sum(x.stt_cost_usd for x in active)
                                      + (getattr(job, "stt_cost_usd", 0.0) if self.current else 0.0), 4),
                "time_left_s": None if time_left is None else round(time_left),
                "request": {"url": r.url, "profile": r.profile, "domain": r.domain,
                            "target_lang": r.target_lang, "course_glossary": r.course_glossary},
                "glossary": course_glossary.summary(self.folder) if r.course_glossary else None,
                "lessons": [x.as_dict() for x in active]}

    def save(self) -> None:
        """course.json in the course's service folder; a failure to write it is logged only."""
        r = self.preview.request
        data = {"format": 1, "playlist_id": self.preview.playlist_id, "url": r.url,
                "title": self.preview.title,
                "profile": r.profile, "domain": r.domain, "target_lang": r.target_lang,
                "course_glossary": r.course_glossary, "state": self.state, "started": self.started_wall,
                "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "lessons": [{"position": x.position, "id": x.video.id, "title": x.video.title,
                             "folder": x.folder_name, "status": x.status, "duration": x.video.duration,
                             "cost_usd": round(x.cost_usd, 4), "stt_cost_usd": round(x.stt_cost_usd, 4),
                             "error": x.error} for x in self.lessons]}
        try:
            with self._lock:
                service = youtube.service_dir(self.folder, create=True)
                tmp = service / (COURSE_MARKER + ".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                os.replace(tmp, service / COURSE_MARKER)
        except OSError:
            logger.exception("course marker not written in %s", self.folder)


def result_id(course_folder: Path, lesson_folder_name: str) -> str:
    """The player's id of a lesson's result: `<course folder>|<lesson folder>` (| is in no file name)."""
    return f"{course_folder.name}|{lesson_folder_name}"


class CourseRunner:
    def __init__(self, runner: JobRunner, today=dt.date.today, work_root: Path | None = None):
        self.runner = runner
        self.work_root = Path(work_root or config.WORK_DIR)  # the pipeline's work folders (RunSettings)
        self.today = today
        self._previews: dict[tuple, CoursePreview] = {}
        self._previews_lock = threading.Lock()
        self._course: Course | None = None

    @property
    def core(self):
        return self.runner.core

    @property
    def active(self) -> Course | None:
        course = self._course
        return course if course is not None and course.state == "running" else None

    def current(self) -> Course | None:
        return self._course

    # --- preview ------------------------------------------------------------------------

    def preview(self, request: CourseRequest) -> CoursePreview:
        """The playlist's lessons with what is already done and the estimate. InputError / YouTubeError."""
        info = self.core.fetch(request.url, self.core.youtube_runtimes())
        if not isinstance(info, youtube.PlaylistInfo):
            raise InputError("not_playlist", "The link does not name a YouTube playlist.")
        if not info.entries:
            raise InputError("empty_playlist", "The playlist has no videos.")
        root = Path(self.core.output_root())
        folder = find_course_folder(root, info.id) or root / youtube.safe_name(
            f"{self.today().isoformat()} {info.title}", words=True)
        marker = read_marker(folder) or {}
        known = {x.get("id"): x for x in marker.get("lessons") or [] if isinstance(x, dict)}
        entries = info.entries[:MAX_LESSONS]
        lessons = []
        for position, video in enumerate(entries, start=1):
            old = known.get(video.id) or {}
            name = old.get("folder") or lesson_folder_name(position, video.title, len(entries))
            lessons.append(Lesson(position, video, name, reason=unavailable_reason(video)))
        preview = CoursePreview(request, info.id, info.title, folder, lessons, more=max(0, len(info.entries)
                                                                                         - MAX_LESSONS))
        for lesson in lessons:
            if lesson.available:
                self._estimate(preview, lesson)
        with self._previews_lock:
            now = time.monotonic()
            self._previews = {k: p for k, p in self._previews.items() if now - p.made < PREVIEW_TTL_S}
            self._previews[request.key()] = preview
        return preview

    def _lesson_request(self, request: CourseRequest, video: youtube.VideoInfo) -> JobRequest:
        return JobRequest(mode="url", url=f"https://www.youtube.com/watch?v={video.id}",
                          profile=request.profile, domain=request.domain, target_lang=request.target_lang)

    def _estimate(self, preview: CoursePreview, lesson: Lesson) -> None:
        """A lesson's cost and time, counting the cache (the context its earlier run stored); never fails."""
        try:
            folder = preview.folder / lesson.folder_name
            lesson.ready = youtube.is_done(folder)
            video = with_stored_context(lesson.video, self.work_root)
            extra = course_glossary.inputs_for(preview.folder, lesson.folder_name) \
                if preview.request.course_glossary else []
            job_preview = self.runner.preview(self._lesson_request(preview.request, video), video=video,
                                              folder=folder, extra_glossaries=extra)
            lesson.cost_known = job_preview.cost_known
            lesson.claude_usd, lesson.stt_usd = job_preview.cost.claude_usd, job_preview.cost.stt_usd
            lesson.time_s = job_preview.time.seconds
            plan = job_preview.plan
            if plan is not None and not plan.claude_usd and not plan.stt_usd:
                lesson.ready = True  # everything paid comes from the cache
        except Exception:
            logger.exception("lesson %s: no estimate", lesson.position)
            lesson.cost_known, lesson.time_s = False, None

    def _cached_preview(self, request: CourseRequest) -> CoursePreview | None:
        with self._previews_lock:
            preview = self._previews.get(request.key())
        if preview is not None and time.monotonic() - preview.made < PREVIEW_TTL_S:
            return preview
        return None

    # --- start --------------------------------------------------------------------------

    def start(self, request: CourseRequest) -> Course:
        """Start a course after the cost gate. BusyError, GateError, InputError, YouTubeError."""
        runner = self.runner
        if not runner._busy.acquire(blocking=False):
            raise BusyError(runner._active)
        try:
            preview = self._cached_preview(request) or self.preview(request)
            preview = CoursePreview(request, preview.playlist_id, preview.title, preview.folder,
                                    [Lesson(**{**x.__dict__}) for x in preview.lessons], preview.more)
            chosen = preview.chosen(request.selected)
            if not chosen:
                raise InputError("nothing_selected", "Choose at least one lesson.")
            self._gate(request, preview)
            for lesson in chosen:
                lesson.status = "queued"
            course = Course(id=uuid.uuid4().hex[:12], preview=preview,
                            started_wall=time.strftime("%Y-%m-%dT%H:%M:%S"))
            course.folder.mkdir(parents=True, exist_ok=True)
            course.save()
            self._course = course
            threading.Thread(target=self._run, args=(course,), name=f"course-{course.id}",
                             daemon=True).start()
            return course
        except BaseException:
            runner._busy.release()
            raise

    def _gate(self, request: CourseRequest, preview: CoursePreview) -> None:
        limit = self.runner.limit_usd
        totals = preview.totals(request.selected)
        if not totals["known"]:
            if not request.confirmed_no_estimate:
                raise GateError("confirm_no_estimate", "Some lessons have no cost estimate; confirm that you "
                                                       "want to start anyway.")
            return
        amount = totals["total_usd"]
        if amount <= limit:
            return
        if request.confirmed_cost_usd is None:
            raise GateError("confirm_cost", f"The course estimate {amount:.2f} USD is above the limit of "
                                            f"{limit:.2f} USD; confirm this amount to start.", amount)
        if abs(request.confirmed_cost_usd - amount) > 0.005:
            raise GateError("cost_mismatch", f"The confirmed amount {request.confirmed_cost_usd:.2f} USD is "
                                             f"not the estimate {amount:.2f} USD; estimate again.", amount)

    def stop_after_current(self, course_id: str) -> bool:
        course = self._course
        if course is None or course.id != course_id or course.state != "running":
            return False
        course.stop_requested = True
        return True

    # --- the worker ---------------------------------------------------------------------

    def _run(self, course: Course) -> None:
        request = course.preview.request
        console.info("Course started: %s (%d lessons)", short(course.preview.title, 100), len(course.queue()))
        try:
            for lesson in list(course.queue()):
                if course.stop_requested:
                    break
                self._run_lesson(course, lesson)
            course.state = "stopped" if course.queue() else "finished"
        except Exception:
            logger.exception("course %s stopped by an error", course.id)
            course.state = "stopped"
        finally:
            course.current, course.current_job = None, None
            course.save()
            self.runner._active = None
            self.runner._busy.release()
        done = sum(1 for x in course.lessons if x.status == "finished")
        failed = sum(1 for x in course.lessons if x.status == "failed")
        console.info("Course %s: %d finished, %d failed, %d left (%s).", course.state, done, failed,
                     len(course.queue()), request.url)

    def _run_lesson(self, course: Course, lesson: Lesson) -> None:
        request, core = course.preview.request, self.core
        lesson.status, lesson.error = "running", None
        course.current, course.current_job = lesson.position, None
        course.save()
        folder = course.folder / lesson.folder_name
        try:
            video = lesson.video
            if not video.description and not video.upload_date:  # a flat entry: the full metadata (context)
                info = core.fetch(video.url, core.youtube_runtimes())
                if isinstance(info, youtube.PlaylistInfo):
                    raise InputError("not_video", "The lesson link names a playlist.")
                video = info
            reuse_download(Path(core.output_root()), video, folder)
            extra = (course_glossary.inputs_for(course.folder, lesson.folder_name, write=True)
                     if request.course_glossary else [])
            job_request = self._lesson_request(request, video)
            preview = self.runner.preview(job_request, video=video, folder=folder, extra_glossaries=extra)

            def follow(job):
                course.current_job = job
                lesson.job_id = job.id

            job = self.runner.run_lesson(job_request, preview, on_job=follow)
            lesson.cost_usd, lesson.stt_cost_usd = job.cost_usd, job.stt_cost_usd
            last = job.events_after(max(0, job.last_event_id - 1))[0]
            final = last[-1] if last else {}
            if job.state == "finished":
                lesson.status, lesson.result = "finished", result_id(course.folder, lesson.folder_name)
                if request.course_glossary:
                    course_glossary.after_lesson(course.folder, lesson.folder_name, lesson.position,
                                                 work_dir(video, self.work_root), request.target_lang)
            else:
                lesson.status = "failed"
                lesson.error = {k: final.get(k) for k in ERROR_KEYS}
        except Exception as e:  # metadata, the folder, the glossary: the lesson fails, the course goes on
            logger.exception("course %s: lesson %s failed outside its job", course.id, lesson.position)
            reason, hint = failure(e)
            lesson.status, lesson.error = "failed", {"reason": reason, "hint": hint, "service": None,
                                                     "kind": None, "status": None, "step": None}
        finally:
            course.current, course.current_job = None, None
            course.save()


def work_dir(video: youtube.VideoInfo, work_root: Path) -> Path:
    """The lesson's pipeline work folder (the same as for the video run alone)."""
    return Path(work_root) / f"yt-{youtube.safe_name(video.id, 40)}"


def with_stored_context(video: youtube.VideoInfo, work_root: Path) -> youtube.VideoInfo:
    """
    A flat playlist entry has no description or chapters, so its context differs from the one its
    earlier run used and the cache would look stale. The pipeline keeps that context in the work
    folder's meta.json; for the estimate, a video whose context is stored there gets it back.
    """
    meta = work_dir(video, work_root) / "meta.json"
    if video.description or not meta.is_file():
        return video
    try:
        stored = str(json.loads(meta.read_text(encoding="utf-8")).get("description") or "")
    except (OSError, ValueError):
        return video
    if not stored.startswith(f"Title: {video.title}"):
        return video

    class _Stored(youtube.VideoInfo):
        def context(self) -> str:
            return stored

    return _Stored(**video.__dict__)


def reuse_download(root: Path, video: youtube.VideoInfo, folder: Path) -> None:
    """
    A video downloaded before for a single run (a result folder directly in the output root) is
    linked into the lesson's folder, so the course does not download it again and its cached steps
    (keyed by the file's content) stay valid. Nothing happens if the lesson already has its file.
    """
    stem = youtube.video_file_stem(video, folder)
    target = folder / f"{stem}.mp4"
    if target.is_file():
        return
    try:
        for meta in [*root.glob(f"*/{youtube.SERVICE_DIR}/meta.json"), *root.glob("*/meta.json")][:MAX_SCAN]:
            other = meta.parent.parent if meta.parent.name == youtube.SERVICE_DIR else meta.parent
            try:
                if json.loads(meta.read_text(encoding="utf-8")).get("id") != video.id:
                    continue
            except (OSError, ValueError):
                continue
            source = other / f"{youtube.video_file_stem(video, other)}.mp4"
            if source.is_file():
                folder.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(source, target)
                except OSError:
                    return  # another drive: the lesson downloads it
                for name in ("meta.json",):  # the download's metadata, so the lesson folder is complete
                    found = youtube.service_file(other, name)
                    if found is not None:
                        (youtube.service_dir(folder, create=True) / name).write_bytes(found.read_bytes())
                return
    except OSError:
        logger.exception("download of %s not reused", video.id)
