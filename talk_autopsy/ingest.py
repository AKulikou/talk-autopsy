"""
Stage 0 input: audio for speech recognition.

    source video or audio file -> 16 kHz mono 16-bit WAV (optionally a cut by timecodes)

ffmpeg and ffprobe must be on PATH. The video itself never leaves the machine.
Downloading from a URL (yt-dlp) arrives with stage 1.
"""

import re
import shutil
import subprocess
from pathlib import Path

SAMPLE_RATE = 16000
# A cut may end up to this far past the probed duration (container rounding).
DURATION_TOLERANCE_S = 0.5

_TIMECODE = re.compile(r"^(?:(?:(\d+):)?(\d{1,2}):)?(\d+(?:\.\d+)?)$")


class ToolNotFoundError(RuntimeError):
    """ffmpeg or ffprobe is not installed or not on PATH."""


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ToolNotFoundError(
            f"{name} not found on PATH. Install ffmpeg (it includes ffprobe): "
            "https://ffmpeg.org/download.html, or 'choco install ffmpeg' / 'apt-get install ffmpeg'."
        )
    return path


def parse_timecode(value: str) -> float:
    """
    Seconds from "SS", "SS.s", "MM:SS" or "HH:MM:SS" (fractions allowed on the seconds).
    Minutes and seconds after a colon must be below 60.
    """
    match = _TIMECODE.match(value.strip())
    if not match:
        raise ValueError(f"Not a timecode: {value!r} (use SS, MM:SS or HH:MM:SS)")
    hours, minutes, seconds = match.groups()
    has_colon = ":" in value
    if has_colon and float(seconds) >= 60:
        raise ValueError(f"Seconds must be below 60 in {value!r}")
    if hours is not None and int(minutes) >= 60:
        raise ValueError(f"Minutes must be below 60 in {value!r}")
    return int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds)


def probe(source: Path) -> float:
    """Duration of a media file in seconds (ffprobe)."""
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"No such file: {source}")
    result = subprocess.run(
        [_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(source)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {source}: {result.stderr.strip()}")
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise RuntimeError(f"ffprobe returned no duration for {source}: {result.stdout.strip()!r}") from None


def extract_audio(source: Path, out_wav: Path, start: float | None = None, end: float | None = None) -> Path:
    """
    Write the audio of `source` as 16 kHz mono 16-bit PCM WAV, optionally only
    the part from `start` to `end` (seconds).

    A cut outside the file is an error, not a silently shorter result. The WAV
    appears only when ffmpeg succeeded (written to a temporary name first).
    """
    source, out_wav = Path(source), Path(out_wav)
    duration = probe(source)
    begin = 0.0 if start is None else start
    finish = duration if end is None else end
    if begin < 0:
        raise ValueError(f"start must not be negative, got {begin}")
    if begin >= duration:
        raise ValueError(f"start {begin:.2f} s is past the end of {source.name} ({duration:.2f} s)")
    if finish <= begin:
        raise ValueError(f"end ({finish:.2f} s) must be after start ({begin:.2f} s)")
    if finish > duration + DURATION_TOLERANCE_S:
        raise ValueError(f"end {finish:.2f} s is past the end of {source.name} ({duration:.2f} s)")

    command = [_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y"]
    if begin > 0:
        command += ["-ss", f"{begin:.3f}"]
    if end is not None:
        command += ["-t", f"{finish - begin:.3f}"]
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    partial = out_wav.with_name(out_wav.stem + ".partial.wav")
    command += ["-i", str(source), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
                str(partial)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed on {source}: {result.stderr.strip()}")
    partial.replace(out_wav)
    return out_wav


def download(url: str, out_dir: Path) -> Path:
    """
    Download the best audio of a video URL with yt-dlp into `out_dir` and return
    the file path. Stage 1.
    """
    raise NotImplementedError("stage 1")


def compress_audio(wav: Path, out_flac: Path) -> Path:
    """
    A FLAC copy of a WAV (lossless, about half the size), for upload to a cloud
    speech recognition engine; written to a temporary name first.
    """
    wav, out_flac = Path(wav), Path(out_flac)
    if not wav.is_file():
        raise FileNotFoundError(f"No such file: {wav}")
    out_flac.parent.mkdir(parents=True, exist_ok=True)
    partial = out_flac.with_name(out_flac.name + ".part.flac")
    command = [_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
               "-c:a", "flac", "-compression_level", "8", str(partial)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg could not compress {wav.name}: {result.stderr.strip()[-500:]}")
    partial.replace(out_flac)
    return out_flac
