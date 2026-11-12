"""
The two ways in: a YouTube video link or a file on this computer. Each check
raises InputError with a short code the page translates and an English message.

A local path must be absolute, exist, be a regular file and carry a media
extension; it is read in place by the pipeline and never served, opened or
executed by the web server.
"""

from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
                           "youtu.be", "www.youtu.be"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".wmv", ".ts", ".mpg",
                              ".mpeg", ".flv", ".3gp"})
AUDIO_EXTENSIONS = frozenset({".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma"})
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
MAX_URL_CHARS = 2000
MAX_PATH_CHARS = 1000


class InputError(ValueError):
    """A request the server will not act on; `code` is for the page, the message for people and logs."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


PLAYLIST_MESSAGE = ("Playlists are not supported in the web page yet: use the desktop launcher "
                    "or `talk-autopsy run <playlist link>`.")


def normalize_youtube_url(text: str) -> str:
    """
    A YouTube video link -> https://www.youtube.com/watch?v=<id>. A link to a
    video inside a playlist (`watch?v=...&list=...`) means that one video; a link
    to a playlist alone is refused (InputError `playlist`).
    """
    value = text.strip()
    if not value:
        raise InputError("neither_given", "Paste a YouTube link.")
    if len(value) > MAX_URL_CHARS:
        raise InputError("bad_url", "The link is too long.")
    parsed = urlparse(value if "://" in value else "https://" + value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or host not in YOUTUBE_HOSTS:
        raise InputError("bad_url", "This is not a YouTube link.")
    query = parse_qs(parsed.query)
    video_id = ""
    if host.endswith("youtu.be"):
        video_id = parsed.path.strip("/").split("/")[0]
    elif parsed.path in ("/watch", "/watch/"):
        video_id = (query.get("v") or [""])[0]
    elif parsed.path.startswith(("/shorts/", "/live/", "/embed/", "/v/")):
        video_id = parsed.path.split("/")[2] if len(parsed.path.split("/")) > 2 else ""
    if not video_id and (parsed.path.startswith("/playlist") or "list" in query):
        raise InputError("playlist", PLAYLIST_MESSAGE)
    if not video_id or not all(c.isalnum() or c in "-_" for c in video_id) or len(video_id) > 20:
        raise InputError("bad_url", "The link does not name a YouTube video.")
    return "https://www.youtube.com/watch?" + urlencode({"v": video_id})


def check_media_path(text: str) -> Path:
    """An absolute path to an existing regular media file, or InputError."""
    value = text.strip().strip('"').strip("'").strip()
    if not value:
        raise InputError("neither_given", "Choose a file.")
    if len(value) > MAX_PATH_CHARS or "\x00" in value:
        raise InputError("bad_path", "The path is not valid.")
    path = Path(value)
    if not path.is_absolute():
        raise InputError("not_absolute", "Give the full path to the file (starting with a drive letter).")
    try:
        exists, is_file = path.exists(), path.is_file()
    except OSError:
        raise InputError("bad_path", "The path is not valid.") from None
    if not exists:
        raise InputError("no_such_file", "There is no such file.")
    if not is_file:
        raise InputError("not_a_file", "This is a folder, not a file.")
    if path.suffix.lower() not in MEDIA_EXTENSIONS:
        raise InputError("bad_extension", "This is not a video or audio file "
                                          f"({', '.join(sorted(MEDIA_EXTENSIONS))}).")
    return path


def is_audio_only(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS
