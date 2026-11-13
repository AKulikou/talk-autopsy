"""
Finished results in the output root, for the start page's recent list and the
in-browser player (docs/web.md).

- A result is a folder directly inside the output root with a marker: the web
  page's `talk-autopsy-result.json` (title, target language, files) or the
  CLI's `done.json` (YouTube runs). The scan is read-only and bounded
  (MAX_SCAN folders, newest first, MAX_LIST listed).
- A result and a file are named by one path component each, resolved and
  checked to sit directly inside the output root (or the result folder); the
  client never gives a path that is used as it is.
- The player prefers a video without burned-in subtitles (the downloaded
  original `<stem>.mp4`, then the subtitle-track `<stem>.subs.mp4`). With only
  the burned-in `<stem>.<lang>.mp4`, the target-language track is not offered
  at all (that text is already in the picture; showing it again drew two
  copies), only English, starting off (`burned_lang` names the language in the
  picture; the owner's report, 2026-11-13). An audio result gets an audio
  player. Subtitles go to the browser as WebVTT, converted
  from the result's `.srt` on request (no extra pipeline output).
"""

import json
import re
from pathlib import Path

from talk_autopsy import youtube
from talk_autopsy.web import labels
from talk_autopsy.web import settings as web_settings
from talk_autopsy.web.jobs import RESULT_MARKER

MAX_SCAN = 300
MAX_LIST = 12
VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm"}
AUDIO_TYPES = {".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
               ".opus": "audio/ogg", ".aac": "audio/aac", ".flac": "audio/flac"}
MEDIA_TYPES = {**VIDEO_TYPES, **AUDIO_TYPES}
_SRT_TIME = re.compile(r"(\d{1,2}:\d{2}:\d{2}),(\d{3})")


def _one_component(name: str) -> bool:
    """A plain name: no separator, no parent reference; the resolved path is checked again after this."""
    if not name or len(name) > 255 or name in (".", ".."):
        return False
    return not any(c in name for c in ("/", "\\", "\x00"))


def folder_of(root: Path, result_id: str) -> Path | None:
    """The result folder `result_id` directly inside `root`, or None."""
    if not _one_component(result_id):
        return None
    try:
        base = Path(root).resolve()
        folder = (base / result_id).resolve()
    except (OSError, RuntimeError):
        return None
    if folder.parent != base or not folder.is_dir() or not _marker(folder):
        return None
    return folder


def file_in(folder: Path, name: str) -> Path | None:
    """The regular file `name` directly inside `folder`, or None."""
    if not _one_component(name):
        return None
    try:
        path = (folder / name).resolve()
    except (OSError, RuntimeError):
        return None
    return path if path.parent == folder and path.is_file() else None


def _marker(folder: Path) -> dict | None:
    """The page's marker, else the CLI's done marker: in the service folder, or at the top (before 1.1)."""
    for name in (RESULT_MARKER, youtube.DONE_MARKER):
        path = youtube.service_file(folder, name)
        if path is not None:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            return data if isinstance(data, dict) else {}
    return None


def choose_media(files: list[str], stem: str) -> tuple[str | None, bool]:
    """(file to play, subtitles burned in): the original, the subtitle-track MP4, a burned-in MP4, audio."""
    for name in (f"{stem}.mp4", f"{stem}.subs.mp4"):
        if name in files:
            return name, False
    for name in files:
        if name.startswith(f"{stem}.") and name.endswith(".mp4"):
            return name, True
    for name in files:
        if name.startswith(f"{stem}.") and Path(name).suffix.lower() in AUDIO_TYPES:
            return name, False
    return None, False


def describe(folder: Path) -> dict:
    """Title, date, language, files, the media to play and its subtitle tracks."""
    marker = _marker(folder) or {}
    files = sorted(p.name for p in folder.iterdir() if p.is_file() and not p.name.endswith(".partial.mp4"))
    stem = labels.stem_of(files)
    lang = str(marker.get("target_lang") or "")
    media, burned = choose_media(files, stem) if stem else (None, False)
    burned_lang = media[len(stem) + 1:-4] if media and burned else ""  # <stem>.<lang>.mp4
    if burned and not lang:
        lang = burned_lang
    tracks = []
    if stem and f"{stem}.srt" in files and not (burned and burned_lang in ("", lang)):
        name = web_settings.language_name(lang) if lang else ""
        tracks.append({"file": f"{stem}.srt", "srclang": lang or "und", "label": name or lang or "subtitles",
                       "default": not burned})
    if stem and f"{stem}.en.srt" in files:
        tracks.append({"file": f"{stem}.en.srt", "srclang": "en", "label": "English", "default": False})
    kind = None
    if media:
        kind = "video" if Path(media).suffix.lower() in VIDEO_TYPES else "audio"
    finished = str(marker.get("finished") or "")
    if not finished:
        try:
            finished = _mtime_text(folder)
        except OSError:
            finished = ""
    return {"id": folder.name, "folder": str(folder), "title": str(marker.get("title") or folder.name),
            "finished": finished, "target_lang": lang,
            "files": [{"name": f, "kind": labels.file_kind(f, stem, lang)} for f in files
                      if f not in (RESULT_MARKER, youtube.DONE_MARKER, "meta.json")],
            "media": media, "kind": kind, "burned": burned, "burned_lang": burned_lang, "tracks": tracks,
            "cost_usd": marker.get("cost_usd"), "stt_cost_usd": marker.get("stt_cost_usd"),
            "untranslated": _ranges(marker.get("untranslated")),
            "uncorrected": _ranges(marker.get("uncorrected")),
            "replay": bool(marker.get("replay"))}


def _ranges(value) -> list[list[float]]:
    """[[start, end], ...] from a marker (written by the job), anything else -> []."""
    try:
        return [[float(a), float(b)] for a, b in value or []][:50]
    except (TypeError, ValueError):
        return []


def _mtime_text(folder: Path) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(folder.stat().st_mtime).isoformat(timespec="seconds")


def recent(root: Path, limit: int = MAX_LIST) -> list[dict]:
    """The newest finished results in `root` (read-only, bounded)."""
    root = Path(root)
    if not root.is_dir():
        return []
    try:
        folders = [p for p in root.iterdir() if p.is_dir()]
        folders.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    found = []
    for folder in folders[:MAX_SCAN]:
        if _marker(folder) is None:
            continue
        try:
            found.append(describe(folder))
        except OSError:
            continue
        if len(found) >= limit:
            break
    found.sort(key=lambda r: r["finished"], reverse=True)
    return found


def srt_to_vtt(text: str) -> str:
    """WebVTT for the browser's <track> from an .srt: the header, '.' in times, cue numbers kept."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    out = ["WEBVTT", ""]
    for block in re.split(r"\n{2,}", text.strip()):
        lines = block.split("\n")
        timing = next((k for k, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue
        lines[timing] = _SRT_TIME.sub(r"\1.\2", lines[timing])
        cue = [line for line in lines[timing:] if line.strip() or line is lines[timing]]
        out.extend(cue)
        out.append("")
    return "\n".join(out)
