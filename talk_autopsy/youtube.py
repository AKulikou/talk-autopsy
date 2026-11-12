"""
YouTube input: a video or playlist link -> a local MP4 in the output folder,
then the ordinary pipeline on that file.

    URL -> metadata (no download): title, channel, duration, description,
           chapters, caption languages -> estimate printed
        -> video up to 1080p as MP4 with audio (H.264 preferred, for TVs)
           + English captions if the author provided them (saved, never used by
           the frozen speech recognition path)
        -> <OUTPUT_DIR>/<upload-date> <title>/  (meta.json, the MP4, captions)

yt-dlp is used as a library (`yt_dlp.YoutubeDL`), never as a subprocess. Since
late 2025 YouTube needs a JavaScript challenge solver: the `yt-dlp-ejs`
package (installed with `yt-dlp[default]`) run by an external JavaScript runtime
(Deno, Node.js, Bun or QuickJS). Nothing is installed automatically: `doctor`
says what is missing and how to install it, and the YouTube path stops before
any work if no runtime is found.

The video's title, description and chapter titles go to the pipeline as
`--context`: an input to the frozen auto-glossary step, not a change to it.

Tests pass a fake `ydl_factory`; nothing here opens a network connection on
its own.
"""

import datetime as dt
import importlib
import importlib.util
import json
import logging
import re
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from talk_autopsy import config

# Runtimes in the order yt-dlp is asked to try them (Deno is its recommended default).
JS_RUNTIMES = ("deno", "node", "bun", "quickjs")
INSTALL_JS_RUNTIME = "winget install DenoLand.Deno"
UPGRADE_YT_DLP = "uv lock --upgrade-package yt-dlp && uv sync"
FORMAT = ("bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080][ext=mp4]+ba[ext=m4a]"
          "/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b[height<=1080]/b")
CAPTION_LANGS = ["en", "en-US", "en-GB"]
MAX_NAME_CHARS = 80  # keeps <OUTPUT_DIR>/<folder>/<file> well below Windows' 260-character path limit
DONE_MARKER = "done.json"
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

YdlFactory = Callable[[dict], object]  # params -> a YoutubeDL-like context manager
ByteProgress = Callable[[int, int | None], None]  # (bytes done, bytes expected or None)
logger = logging.getLogger(__name__)


class YouTubeError(RuntimeError):
    """yt-dlp could not fetch or download; the message says what to try."""


def failure_hint() -> str:
    return ("YouTube may have changed something. Run `talk-autopsy doctor` (it checks the JavaScript "
            "runtime and the yt-dlp version; upgrade with `" + UPGRADE_YT_DLP + "`). A local file still "
            "works: download the video yourself and run `talk-autopsy run <file>`.")


# --- doctor -----------------------------------------------------------------------


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    required: bool = True  # False: a warning, the tool still works


def _runtime_classes() -> dict:
    """yt-dlp's runtime detectors by name (importing yt_dlp registers them)."""
    globals_module = importlib.import_module("yt_dlp.globals")
    importlib.import_module("yt_dlp")
    return dict(globals_module.supported_js_runtimes.value)


def find_js_runtimes(classes: dict | None = None) -> list[tuple[str, str]]:
    """[(name, version)] of the JavaScript runtimes yt-dlp finds and supports, in JS_RUNTIMES order."""
    classes = _runtime_classes() if classes is None else classes
    found = []
    for name in JS_RUNTIMES:
        cls = classes.get(name)
        if cls is None:
            continue
        info = cls().info
        if info is not None and info.supported:
            found.append((name, info.version))
    return found


def yt_dlp_version() -> str:
    return importlib.import_module("yt_dlp.version").__version__


def version_age_days(version: str, today: dt.date | None = None) -> int | None:
    """Days since a yt-dlp version (YYYY.MM.DD[.N]); None if it is not a date."""
    match = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})", version)
    if not match:
        return None
    released = dt.date(*(int(g) for g in match.groups()))
    return ((today or dt.date.today()) - released).days


def ejs_installed() -> bool:
    try:
        importlib.import_module("yt_dlp_ejs")
    except ImportError:
        return False
    return True


ALIGN_EXTRA_FIX = "uv sync --extra align"
ALIGN_UNAVAILABLE = ("forced alignment is unavailable: the optional `align` extra (torch, torchaudio) is not "
                     "installed; it is needed only for the Gemini fallback. Install it with: "
                     + ALIGN_EXTRA_FIX)


def align_extra_installed() -> bool:
    """True if the optional `align` extra (torch, torchaudio) is installed; found without importing torch."""
    return all(importlib.util.find_spec(name) is not None for name in ("torch", "torchaudio"))


def web_ui_installed() -> bool:
    """True if the web UI's server packages (fastapi, uvicorn) are importable (not imported here)."""
    return all(importlib.util.find_spec(name) is not None for name in ("fastapi", "uvicorn"))


def doctor(which: Callable[[str], str | None] = shutil.which, runtimes: list[tuple[str, str]] | None = None,
           version: str | None = None, ejs: bool | None = None, align: bool | None = None,
           key_is_set: Callable[[str], bool] = config.key_is_set,
           today: dt.date | None = None, web: bool | None = None) -> list[Check]:
    """
    What the tool needs on this machine: ffmpeg and ffprobe, a JavaScript runtime
    yt-dlp can use, the solver package, the yt-dlp version and its age, the
    optional `align` extra (forced alignment, needed only for the Gemini
    fallback), whether the web UI can start (`talk-autopsy web`), and which API
    keys are set (names only, never values). Arguments replace the real probes
    in tests.
    """
    checks = []
    for tool in ("ffmpeg", "ffprobe"):
        path = which(tool)
        checks.append(Check(tool, path is not None, path or "not found on PATH",
                            "winget install Gyan.FFmpeg (or choco install ffmpeg)"))
    runtimes = find_js_runtimes() if runtimes is None else runtimes
    checks.append(Check("JavaScript runtime (YouTube)", bool(runtimes),
                        ", ".join(f"{n} {v}" for n, v in runtimes)
                        or "none of deno, node, bun, quickjs found",
                        INSTALL_JS_RUNTIME))
    ejs = ejs_installed() if ejs is None else ejs
    checks.append(Check("yt-dlp-ejs (YouTube challenge solver)", ejs, "installed" if ejs else "missing",
                        "uv sync (it comes with yt-dlp[default])"))
    version = yt_dlp_version() if version is None else version
    age = version_age_days(version, today)
    fresh = age is not None and age <= config.YT_DLP_MAX_AGE_DAYS
    checks.append(Check("yt-dlp version", fresh,
                        f"{version}" + (f", {age} days old" if age is not None else ""),
                        UPGRADE_YT_DLP, required=False))
    align = align_extra_installed() if align is None else align
    checks.append(Check("align extra (Gemini fallback only)", align,
                        "installed (torch, torchaudio)" if align
                        else "not installed; forced alignment is needed only for the Gemini fallback",
                        ALIGN_EXTRA_FIX, required=False))
    web = web_ui_installed() if web is None else web
    checks.append(Check("web UI (talk-autopsy web)", web,
                        "can start (fastapi, uvicorn installed)" if web else "fastapi or uvicorn is missing",
                        "uv sync", required=False))
    for name in config.SECRET_ENV_VARS:
        if name in ("ANTHROPIC_AUTH_TOKEN", "GOOGLE_API_KEY", "HF_TOKEN"):
            continue  # SDK-read credentials this project does not use
        is_set = key_is_set(name)
        checks.append(Check(name, is_set, "set" if is_set else "not set", "add it to .env",
                            required=name == "ANTHROPIC_API_KEY"))
    return checks


def require_youtube_tools(runtimes: list[tuple[str, str]] | None = None,
                          ejs: bool | None = None) -> list[str]:
    """The runtimes to enable; YouTubeError (with the install command) if YouTube cannot work here."""
    runtimes = find_js_runtimes() if runtimes is None else runtimes
    if not runtimes:
        raise YouTubeError("No JavaScript runtime found; YouTube downloads need one (yt-dlp solves "
                           f"YouTube's challenges with it). Install Deno: {INSTALL_JS_RUNTIME}, open a new "
                           "terminal, then run `talk-autopsy doctor`.")
    if not (ejs_installed() if ejs is None else ejs):
        raise YouTubeError("The yt-dlp-ejs package is missing; run `uv sync` in the project.")
    return [name for name, _ in runtimes]


# --- metadata ---------------------------------------------------------------------


@dataclass
class VideoInfo:
    id: str
    url: str
    title: str
    channel: str = ""
    duration: float | None = None  # seconds
    description: str = ""
    chapters: list[str] = field(default_factory=list)
    upload_date: str = ""  # YYYY-MM-DD
    author_captions: list[str] = field(default_factory=list)  # languages the author provided
    automatic_captions: bool = False

    def context(self) -> str:
        """Title, description and chapter titles: the auto-glossary's --context."""
        parts = [f"Title: {self.title}"]
        if self.channel:
            parts.append(f"Channel: {self.channel}")
        if self.description.strip():
            parts.append(f"Description:\n{self.description.strip()}")
        if self.chapters:
            parts.append("Chapters:\n" + "\n".join(f"- {c}" for c in self.chapters))
        return "\n\n".join(parts)

    def as_meta(self) -> dict:
        return {"id": self.id, "url": self.url, "title": self.title, "channel": self.channel,
                "duration": self.duration, "upload_date": self.upload_date, "description": self.description,
                "chapters": self.chapters, "author_captions": self.author_captions,
                "automatic_captions": self.automatic_captions}


@dataclass
class PlaylistInfo:
    id: str
    title: str
    entries: list[VideoInfo]  # in playlist order; flat entries may lack description and chapters


def _date(value: str | None) -> str:
    if value and re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return ""


def video_from_info(info: dict) -> VideoInfo:
    chapters = [c.get("title", "") for c in info.get("chapters") or [] if c.get("title")]
    url = (info.get("webpage_url") or info.get("url")
           or f"https://www.youtube.com/watch?v={info.get('id', '')}")
    return VideoInfo(id=str(info.get("id") or ""), url=url,
                     title=str(info.get("title") or info.get("id") or ""),
                     channel=str(info.get("channel") or info.get("uploader") or ""),
                     duration=float(info["duration"]) if info.get("duration") else None,
                     description=str(info.get("description") or ""), chapters=chapters,
                     upload_date=_date(info.get("upload_date")),
                     author_captions=sorted((info.get("subtitles") or {}).keys()),
                     automatic_captions=bool(info.get("automatic_captions")))


def _default_factory(params: dict):
    return importlib.import_module("yt_dlp").YoutubeDL(params)


def _base_params(runtimes: Iterable[str]) -> dict:
    return {"quiet": True, "no_warnings": True, "noprogress": True,
            "js_runtimes": {name: {} for name in runtimes}}


def _download_errors() -> tuple[type[BaseException], ...]:
    try:
        utils = importlib.import_module("yt_dlp.utils")
        return (utils.DownloadError, utils.ExtractorError)
    except ImportError:  # pragma: no cover - yt-dlp is a dependency
        return ()


def fetch(url: str, runtimes: Iterable[str],
          ydl_factory: YdlFactory | None = None) -> VideoInfo | PlaylistInfo:
    """Metadata of a video or a playlist (flat: one request for the list), without downloading."""
    factory = ydl_factory or _default_factory
    params = {**_base_params(runtimes), "skip_download": True, "extract_flat": "in_playlist"}
    try:
        with factory(params) as ydl:
            info = ydl.extract_info(url, download=False)
    except _download_errors() as e:
        raise YouTubeError(f"yt-dlp could not read {url}: {e}\n{failure_hint()}") from e
    if info is None:
        raise YouTubeError(f"yt-dlp returned nothing for {url}.\n{failure_hint()}")
    if info.get("_type") == "playlist":
        entries = [video_from_info(e) for e in info.get("entries") or [] if e]
        return PlaylistInfo(id=str(info.get("id") or ""), title=str(info.get("title") or "playlist"),
                            entries=entries)
    return video_from_info(info)


def fetch_video(url: str, runtimes: Iterable[str], ydl_factory: YdlFactory | None = None) -> VideoInfo:
    """Full metadata of one video (a playlist's flat entry lacks description and chapters)."""
    info = fetch(url, runtimes, ydl_factory)
    if isinstance(info, PlaylistInfo):
        raise YouTubeError(f"{url} is a playlist, not a video")
    return info


# --- names, estimate, download -------------------------------------------------------------


def safe_name(text: str, limit: int = MAX_NAME_CHARS) -> str:
    """A file or folder name valid on Windows: no reserved characters or names, no trailing dot or space."""
    name = _FORBIDDEN.sub(" ", text)
    name = re.sub(r"\s+", " ", name).strip()
    name = name[:limit].rstrip(" .")
    if not name:
        name = "video"
    if name.split(".")[0].upper() in _RESERVED:
        name = f"_{name}"
    return name


def video_folder_name(video: VideoInfo) -> str:
    return safe_name(f"{video.upload_date} {video.title}".strip())


def video_file_stem(video: VideoInfo) -> str:
    return safe_name(video.title, limit=60)


@dataclass(frozen=True)
class Estimate:
    minutes: float | None
    claude_usd: float
    stt_usd: float
    pipeline_minutes: float

    @property
    def total_usd(self) -> float:
        return self.claude_usd + self.stt_usd

    def line(self) -> str:
        if self.minutes is None:
            return "duration unknown: no estimate"
        return (f"{self.minutes:.1f} min of video: about {self.claude_usd:.2f} USD Claude + "
                f"{self.stt_usd:.2f} USD AssemblyAI (list price), about {self.pipeline_minutes:.0f} min of "
                "processing plus download (and burn-in, if asked)")


def estimate(duration_seconds: float | None) -> Estimate:
    """Cost and time from the measured per-hour figures in config (before the pipeline's own estimate)."""
    if duration_seconds is None:
        return Estimate(None, 0.0, 0.0, 0.0)
    hours = duration_seconds / 3600
    return Estimate(duration_seconds / 60, round(hours * config.RUN_CLAUDE_USD_PER_HOUR, 2),
                    round(hours * config.RUN_STT_USD_PER_HOUR, 2), hours * config.RUN_MINUTES_PER_HOUR)


@dataclass
class Download:
    video: Path
    captions: list[Path]
    meta: Path


class DownloadProgress:
    """
    A yt-dlp progress hook -> `progress(bytes done, bytes expected)` over every
    part of the download (video and audio streams are separate files before the
    merge). The expected size is the sum of the requested formats' sizes when
    yt-dlp knows them all, else the sum of the parts seen so far (then the bar
    can step back when the next part starts), else None. A display error is
    logged, never raised into yt-dlp.
    """

    def __init__(self, progress: ByteProgress):
        self.progress = progress
        self.done: dict[str, int] = {}
        self.totals: dict[str, int] = {}

    def __call__(self, status: dict) -> None:
        try:
            name = str(status.get("filename") or status.get("tmpfilename") or "")
            if status.get("status") not in ("downloading", "finished") or not name:
                return
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            done = status.get("downloaded_bytes") or 0
            if status.get("status") == "finished":
                done = total = int(total or done or self.done.get(name, 0))
            self.done[name] = int(done)
            if total:
                self.totals[name] = int(total)
            formats = (status.get("info_dict") or {}).get("requested_formats") or []
            sizes = [f.get("filesize") or f.get("filesize_approx") for f in formats]
            expected = (sum(sizes) if sizes and all(sizes) else sum(self.totals.values())) or None
            done_all = sum(self.done.values())
            self.progress(min(done_all, expected) if expected else done_all, expected)
        except Exception:
            logger.exception("download progress not shown")


def download(video: VideoInfo, folder: Path, runtimes: Iterable[str],
             ydl_factory: YdlFactory | None = None, progress: ByteProgress | None = None) -> Download:
    """
    The video (up to 1080p, MP4 with audio, H.264 preferred) and the author's
    English captions, if any, into `folder`; meta.json written first. An MP4
    already there is reused. `progress(bytes done, bytes expected)` is called as
    yt-dlp reports (DownloadProgress).
    """
    factory = ydl_factory or _default_factory
    folder.mkdir(parents=True, exist_ok=True)
    meta = folder / "meta.json"
    meta.write_text(json.dumps(video.as_meta(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    stem = video_file_stem(video)
    target = folder / f"{stem}.mp4"
    wanted = [lang for lang in CAPTION_LANGS if lang in video.author_captions]
    params = {**_base_params(runtimes), "format": FORMAT, "merge_output_format": "mp4",
              "outtmpl": {"default": str(folder / f"{stem}.%(ext)s"),
                          "subtitle": str(folder / f"{stem}.youtube.%(ext)s")},
              "writesubtitles": bool(wanted), "writeautomaticsub": False, "subtitleslangs": wanted,
              "subtitlesformat": "srt/vtt/best", "overwrites": False}
    if progress is not None:
        params["progress_hooks"] = [DownloadProgress(progress)]
    if not target.is_file():
        try:
            with factory(params) as ydl:
                ydl.download([video.url])
        except _download_errors() as e:
            raise YouTubeError(f"yt-dlp could not download {video.url}: {e}\n{failure_hint()}") from e
    if not target.is_file():
        raise YouTubeError(f"yt-dlp finished but {target.name} is not in {folder}.\n{failure_hint()}")
    captions = sorted(p for p in folder.glob(f"{stem}.youtube.*") if p.suffix in (".srt", ".vtt", ".ttml"))
    return Download(target, captions, meta)


def is_done(folder: Path) -> bool:
    return (folder / DONE_MARKER).is_file()


def mark_done(folder: Path, outputs: list[str], cost: dict) -> None:
    (folder / DONE_MARKER).write_text(
        json.dumps({"finished": dt.datetime.now().isoformat(timespec="seconds"), "outputs": outputs,
                    "cost": cost}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@dataclass
class PlaylistPlan:
    todo: list[VideoInfo]
    skipped_done: list[VideoInfo]
    over_cap: list[VideoInfo]  # stopped before these: the next would cross the cap
    estimate_usd: float


def plan_playlist(playlist: PlaylistInfo, parent: Path, max_videos: int | None, max_total_usd: float,
                  max_video_claude_usd: float) -> PlaylistPlan:
    """
    Videos in playlist order, up to `max_videos`; finished ones (done marker)
    skipped; the list stops before the first video whose estimate would take the
    total past `max_total_usd` or whose Claude estimate is above
    `max_video_claude_usd` (and before a video of unknown duration, which cannot
    be estimated).
    """
    entries = playlist.entries[:max_videos] if max_videos else list(playlist.entries)
    todo, done, over = [], [], []
    total = 0.0
    for index, video in enumerate(entries):
        if is_done(parent / video_folder_name(video)) or _done_by_id(parent, video.id):
            done.append(video)
            continue
        e = estimate(video.duration)
        too_much = e.claude_usd > max_video_claude_usd or total + e.total_usd > max_total_usd
        if video.duration is None or too_much:
            over = [v for v in entries[index:] if v not in done]
            break
        todo.append(video)
        total += e.total_usd
    return PlaylistPlan(todo, done, over, round(total, 2))


def _done_by_id(parent: Path, video_id: str) -> bool:
    """A finished video found by its id in meta.json (a flat entry may lack the upload date in its name)."""
    if not parent.is_dir() or not video_id:
        return False
    for meta in parent.glob(f"*/{'meta.json'}"):
        if is_done(meta.parent):
            try:
                if json.loads(meta.read_text(encoding="utf-8")).get("id") == video_id:
                    return True
            except (OSError, json.JSONDecodeError):
                continue
    return False
