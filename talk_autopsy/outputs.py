"""
Output formats for watching anywhere (after the pipeline's srt step).

    work/<video-id>/<video-id>.<lang>.srt/.ass, <video-id>.en.srt/.ass, <lang>.json
      -> <dest>/<stem>.srt            target language, named like the video so players auto-load it
         <dest>/<stem>.en.srt         English
         <dest>/<stem>.ass, <stem>.en.ass
         <dest>/<stem>.bilingual.ass  English small on top, target language below, same cues
         <dest>/<stem>.subs.mp4       the video with target and English subtitle tracks (mov_text)
         <dest>/<stem>.<lang>.mp4     target subtitles burned into the picture (H.264): for TVs

`--output` takes a list (default `srt,ass`; `all` = every format). The
burned-in file is the one for a TV whose player shows neither `.ass` nor
`.srt`. Its name differs from the video's, so a player that auto-loads
`<name>.srt` does not show subtitles twice over burned ones.

ffmpeg does the MP4 work: subtitle tracks are added without re-encoding
(`-c copy`, `mov_text`); burn-in re-encodes the video with libx264 (`-preset
veryfast -crf 20`), copies the audio and draws the `.ass` file with the
`subtitles` filter (libass), run from the output folder with a plain file name
so that no Windows path needs filter escaping. Measured on the owner's laptop
(2026-11-10, 1080p H.264): 10.7 s for a 60 s clip (0.18x real time), 457 s for a
27:52 talk together with the subtitle-track MP4 (0.27x; the estimate uses 0.27).

Burn-in progress: with `position`, ffmpeg runs with `-progress pipe:1` and
every `out_time_us` it reports is passed on as (seconds encoded, seconds of
video), so a display can show the real position. Without it nothing changes.

No CLI I/O here; `run_ffmpeg` and `popen` are injectable for tests.
"""

import logging
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from talk_autopsy import ingest, subtitles
from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Transcript

FORMATS = ("srt", "ass", "bilingual", "mp4-soft", "mp4-burned")
DEFAULT = "srt,ass"
BURN_SPEED = 0.27  # encoding seconds per second of 1080p video, measured 2026-11-10 (see the docstring)
BURN_FILTER_NAME = "burn-subtitles.ass"  # plain name: the subtitles filter needs no path escaping
LANG3 = {"ru": "rus", "en": "eng", "de": "deu", "fr": "fra", "es": "spa", "uk": "ukr", "it": "ita",
         "pt": "por", "pl": "pol", "zh": "zho", "ja": "jpn"}  # ISO 639-2 tags for the MP4 tracks

logger = logging.getLogger(__name__)
Position = Callable[[float, float | None], None]  # (seconds encoded, seconds of video or None)

# Bilingual .ass: the English line smaller and dimmer on top, the target language below at the usual size.
ENGLISH_FONT_SIZE = 30
ENGLISH_LINE_CHARS = 64


class OutputError(RuntimeError):
    """An output cannot be written (a missing input, ffmpeg failed)."""


def parse_outputs(value: str) -> list[str]:
    """`srt,ass`, `mp4-burned`, `all`, ... -> formats in FORMATS order; ValueError on an unknown name."""
    names = [n.strip().lower() for n in value.split(",") if n.strip()]
    if not names:
        raise ValueError("give at least one output format")
    if "all" in names:
        return list(FORMATS)
    unknown = [n for n in names if n not in FORMATS]
    if unknown:
        raise ValueError(f"unknown output format(s) {', '.join(unknown)}; known: {', '.join(FORMATS)}, all")
    return [f for f in FORMATS if f in names]


def burn_seconds(duration_seconds: float) -> float:
    return duration_seconds * BURN_SPEED


# --- bilingual .ass ---------------------------------------------------------------------


def english_for_cues(transcript: Transcript, cues: list[subtitles.Cue]) -> list[str]:
    """
    The English words under each target-language cue: a translated unit keeps its
    English words with their times, so each word goes to the cue of its segment
    that starts at or before it (the first cue takes the words before it).
    """
    by_segment: dict[int, list[subtitles.Cue]] = {}
    for cue in cues:
        by_segment.setdefault(cue.segment_id, []).append(cue)
    english: dict[int, list[str]] = {id(c): [] for c in cues}
    for segment in transcript.segments:
        segment_cues = by_segment.get(segment.id, [])
        if not segment_cues:
            continue
        for word in segment.words or []:
            owner = segment_cues[0]
            for cue in segment_cues:
                if cue.start <= word.start + 1e-6:
                    owner = cue
            english[id(owner)].append(word.text)
    return [" ".join(english[id(c)]) for c in cues]


def bilingual_ass_text(transcript: Transcript, glossary: Glossary | None = None, title: str = "") -> str:
    """
    One `.ass` with two styles: `English` (small, on top) and `Default` (the
    target language, the usual style below); each event holds both, so they
    share the cue timing of the target-language subtitles.
    """
    cues = subtitles.build_cues(transcript, glossary)
    if not cues:
        raise OutputError(f"{transcript.source}: no text to write as subtitles")
    english = english_for_cues(transcript, cues)
    base = subtitles.ass_text([], title)
    english_style = base.splitlines()
    style_line = next(line for line in english_style if line.startswith("Style: Default,"))
    fields = style_line[len("Style: "):].split(",")
    fields[0], fields[2] = "English", str(ENGLISH_FONT_SIZE)
    fields[3] = "&H00D8D8D8"  # light grey: the target language stays the brighter line
    lines = base.rstrip("\n").splitlines()
    lines.insert(lines.index(style_line) + 1, "Style: " + ",".join(fields))
    br = subtitles.ASS_LINE_BREAK
    for cue, en in zip(cues, english, strict=True):
        en_lines = subtitles.wrap(en, ENGLISH_LINE_CHARS, 2) if en else []
        top = br.join(subtitles.ass_escape(x) for x in en_lines)
        bottom = br.join(subtitles.ass_escape(x) for x in cue.lines)
        text = (r"{\rEnglish}" + top + br + r"{\rDefault}" + bottom) if top else bottom
        lines.append(f"Dialogue: 0,{subtitles.ass_timestamp(cue.start)},{subtitles.ass_timestamp(cue.end)},"
                     f"Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


# --- ffmpeg commands ------------------------------------------------------------------------


def soft_command(ffmpeg: str, video: Path, target_srt: Path, english_srt: Path, out: Path,
                 lang: str) -> list[str]:
    """The video with two subtitle tracks (target first and default, English second); nothing re-encoded."""
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video), "-i", str(target_srt), "-i", str(english_srt),
            "-map", "0:v:0", "-map", "0:a?", "-map", "1:0", "-map", "2:0",
            "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text",
            "-metadata:s:s:0", f"language={LANG3.get(lang, lang)}",
            "-metadata:s:s:1", "language=eng",
            "-disposition:s:0", "default", "-disposition:s:1", "0",
            str(out)]


def burn_command(ffmpeg: str, video: Path, ass_name: str, out: Path) -> list[str]:
    """Subtitles drawn into the picture: H.264 veryfast CRF 20, audio copied; run from the .ass folder."""
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
            "-vf", f"subtitles={ass_name}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(out)]


# --- writing ------------------------------------------------------------------------------


@dataclass
class Written:
    format: str
    path: Path


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise OutputError("ffmpeg not found on PATH; run talk-autopsy doctor")
    return path


def _run(command: list[str], run_ffmpeg: Callable, cwd: Path | None = None) -> None:
    result = run_ffmpeg(command, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise OutputError(f"ffmpeg failed: {(result.stderr or '').strip()[-500:]}")


def with_progress(command: list[str]) -> list[str]:
    """An ffmpeg command that also writes machine-readable progress to stdout (before the output file)."""
    return [*command[:-1], "-progress", "pipe:1", "-nostats", command[-1]]


def _run_with_position(command: list[str], duration: float | None, position: Position, popen: Callable,
                       cwd: Path | None = None) -> None:
    """Run ffmpeg, passing each reported `out_time_us` on as seconds; OutputError on failure."""
    process = popen(with_progress(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    cwd=cwd, encoding="utf-8", errors="replace")
    errors: list[str] = []
    reader = threading.Thread(target=lambda: errors.append(process.stderr.read()), daemon=True)
    reader.start()
    for line in process.stdout:
        key, _, value = line.strip().partition("=")
        if key != "out_time_us" or not value.strip().isdigit():
            continue
        try:
            seconds = int(value) / 1_000_000
            position(min(seconds, duration) if duration else seconds, duration)
        except Exception:
            logger.exception("burn-in progress not shown")
    returncode = process.wait()
    reader.join(timeout=5)
    if returncode != 0:
        raise OutputError(f"ffmpeg failed: {''.join(errors).strip()[-500:]}")


def _duration(video: Path) -> float | None:
    try:
        seconds = ingest.probe(video)
        return seconds if seconds and seconds > 0 else None
    except Exception:
        logger.exception("duration of %s not read; burn-in progress without a total", video.name)
        return None


def write_outputs(work_dir: Path, video_id: str, lang: str, dest: Path, stem: str, formats: list[str],
                  video: Path | None = None, glossary: Glossary | None = None,
                  translated: Transcript | None = None, run_ffmpeg: Callable = subprocess.run,
                  progress: Callable[[str], None] | None = None, position: Position | None = None,
                  popen: Callable = subprocess.Popen, duration: float | None = None) -> list[Written]:
    """
    Write the requested `formats` for a finished run into `dest`, named after
    `stem`. MP4 formats need `video`; `bilingual` needs `translated` (the
    <lang>.json transcript). Partial MP4s are written to a temporary name first.
    `position(seconds encoded, seconds of video)` follows the burn-in when given
    (`duration`, or ffprobe's, is the total).
    """
    progress = progress or (lambda message: None)
    dest.mkdir(parents=True, exist_ok=True)
    target_srt, english_srt = work_dir / f"{video_id}.{lang}.srt", work_dir / f"{video_id}.en.srt"
    target_ass, english_ass = target_srt.with_suffix(".ass"), english_srt.with_suffix(".ass")
    written: list[Written] = []

    def need(path: Path) -> Path:
        if not path.is_file():
            raise OutputError(f"{path} is missing; run the pipeline's srt step first")
        return path

    def copy(source: Path, name: str, fmt: str) -> None:
        out = dest / name
        if source.resolve() != out.resolve():
            shutil.copyfile(need(source), out)
        written.append(Written(fmt, out))

    if "srt" in formats:
        copy(target_srt, f"{stem}.srt", "srt")
        if english_srt.is_file():
            copy(english_srt, f"{stem}.en.srt", "srt")
    if "ass" in formats:
        copy(target_ass, f"{stem}.ass", "ass")
        if english_ass.is_file():
            copy(english_ass, f"{stem}.en.ass", "ass")
    if "bilingual" in formats:
        if translated is None:
            raise OutputError("the bilingual output needs the translated transcript")
        out = dest / f"{stem}.bilingual.ass"
        out.write_text(bilingual_ass_text(translated, glossary, stem), encoding="utf-8-sig", newline="\n")
        written.append(Written("bilingual", out))
    mp4 = [f for f in ("mp4-soft", "mp4-burned") if f in formats]
    if mp4:
        if video is None or not Path(video).is_file():
            raise OutputError("the MP4 outputs need the source video")
        ffmpeg = _ffmpeg()
    if "mp4-soft" in formats:
        out = dest / f"{stem}.subs.mp4"
        partial = out.with_name(out.stem + ".partial.mp4")
        progress(f"mp4-soft: adding subtitle tracks to {Path(video).name} (no re-encoding)")
        try:
            _run(soft_command(ffmpeg, Path(video), need(target_srt), need(english_srt), partial, lang),
                 run_ffmpeg)
        except OutputError:
            partial.unlink(missing_ok=True)
            raise
        partial.replace(out)
        written.append(Written("mp4-soft", out))
    if "mp4-burned" in formats:
        out = dest / f"{stem}.{lang}.mp4"
        partial = out.with_name(out.stem + ".partial.mp4")
        ass_copy = dest / BURN_FILTER_NAME
        shutil.copyfile(need(target_ass), ass_copy)
        progress(f"mp4-burned: drawing the {lang} subtitles into the picture (H.264 re-encode)")
        command = burn_command(ffmpeg, Path(video).resolve(), BURN_FILTER_NAME, partial.resolve())
        try:
            if position is None:
                _run(command, run_ffmpeg, cwd=dest)
            else:
                _run_with_position(command, duration or _duration(Path(video)), position, popen, cwd=dest)
        except OutputError:
            partial.unlink(missing_ok=True)
            raise
        finally:
            ass_copy.unlink(missing_ok=True)
        partial.replace(out)
        written.append(Written("mp4-burned", out))
    return written
