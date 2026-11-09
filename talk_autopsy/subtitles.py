"""
Subtitle files.

    <target_lang>.json -> <name>.<target_lang>.srt
    fixed.json         -> <name>.en.srt (the corrected English)

Built in stage 2 as a viewing tool, ahead of stage 3, so the owner can watch
results; hypothesis D1 (compression for reading speed) is measured in stage 3.

Cues never cross a segment boundary: segments are the unit every stage keeps,
and a translated segment has no finer timing. Inside a segment:

- **Timing.** With word timings whose words are the segment text's words
  (an aligned transcript), a cue runs from its first word's start to its last
  word's end. Without them (a translation), the segment's time is shared out
  by character count.
- **Splitting.** A cue holds at most MAX_LINES lines of MAX_LINE_CHARS
  characters and lasts at most MAX_SECONDS. Among the break points that keep a
  cue within both limits, the latest sentence end (. ? !) is taken, else the
  latest clause end (, ; : and dashes), else the latest word boundary; a break
  is never placed inside a glossary term of several words.
- **Duration.** A cue shorter than MIN_SECONDS is extended into the silence
  after it, never over the next cue; every cue lingers LINGER_SECONDS after its
  last word when there is room. Cues that still miss 1–7 s are counted in the
  report.
- **Lines.** A cue longer than one line is wrapped into two at the space that
  balances the lines best.

`report` gives the reading speed per cue (metrics.reading_speed) and the cues
outside the limits.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from talk_autopsy import metrics
from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Transcript

MAX_LINE_CHARS = 42
MAX_LINES = 2
MIN_SECONDS = 1.0
MAX_SECONDS = 7.0
LINGER_SECONDS = 0.3
GAP_SECONDS = 0.04  # the smallest gap kept between two cues

_SENTENCE_END = re.compile(r"[.?!…][\"')\]»]*$")
_CLAUSE_END = re.compile(r"([,;:]|—|–)[\"')\]»]*$|^[—–-]$")


@dataclass
class Unit:
    """One word of a segment with its time."""

    text: str
    start: float
    end: float


@dataclass
class Cue:
    index: int
    start: float
    end: float
    lines: list[str]
    segment_id: int

    @property
    def text(self) -> str:
        return " ".join(self.lines)


def _units(segment) -> list[Unit]:
    chunks = segment.text.split()
    words = segment.words or []
    if words and [w.text for w in words] == chunks:
        return [Unit(w.text, w.start, max(w.end, w.start)) for w in words]
    total = sum(len(c) + 1 for c in chunks) or 1
    span, t, units = segment.end - segment.start, segment.start, []
    for chunk in chunks:
        length = span * (len(chunk) + 1) / total
        units.append(Unit(chunk, t, t + length))
        t += length
    return units


def _protected(units: list[Unit], glossary: Glossary | None) -> set[int]:
    """Break positions (a break at k = between unit k-1 and unit k) inside a multi-word glossary term."""
    if glossary is None:
        return set()
    words = [re.sub(r"^\W+|\W+$", "", u.text).casefold() for u in units]
    blocked: set[int] = set()
    for entry in glossary.entries:
        parts = [p.casefold() for p in entry.term.split()]
        if len(parts) < 2:
            continue
        for i in range(len(words) - len(parts) + 1):
            if words[i:i + len(parts) - 1] == parts[:-1] and words[i + len(parts) - 1].startswith(parts[-1]):
                blocked.update(range(i + 1, i + len(parts)))
    return blocked


def wrap(text: str, max_chars: int = MAX_LINE_CHARS) -> list[str]:
    """One line if it fits, else two lines split at the space that balances them best."""
    if len(text) <= max_chars:
        return [text]
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    if not spaces:
        return [text]
    best = min(spaces, key=lambda i: (max(i, len(text) - i - 1), -int(text[i - 1] in ",;:.?!")))
    return [text[:best], text[best + 1:]]


def _fits(text: str) -> bool:
    lines = wrap(text)
    return len(lines) <= MAX_LINES and all(len(line) <= MAX_LINE_CHARS for line in lines)


def _split(units: list[Unit], blocked: set[int]) -> list[tuple[int, int]]:
    """[(i, j)] unit ranges, one per cue."""
    ranges, i = [], 0
    while i < len(units):
        candidates = []
        for j in range(i + 1, len(units) + 1):
            text = " ".join(u.text for u in units[i:j])
            if j > i + 1 and (not _fits(text) or units[j - 1].end - units[i].start > MAX_SECONDS):
                break
            if j == len(units) or j not in blocked:
                candidates.append(j)
        if not candidates:  # a term longer than a cue: break inside it rather than nowhere
            candidates = [i + 1]
        if candidates[-1] == len(units):
            ranges.append((i, len(units)))
            break
        sentence = [j for j in candidates if _SENTENCE_END.search(units[j - 1].text)]
        clause = [j for j in candidates if _CLAUSE_END.search(units[j - 1].text)]
        end = (sentence or clause or candidates)[-1]
        ranges.append((i, end))
        i = end
    return ranges


def build_cues(transcript: Transcript, glossary: Glossary | None = None) -> list[Cue]:
    cues: list[Cue] = []
    for segment in transcript.segments:
        units = _units(segment)
        if not units:
            continue
        blocked = _protected(units, glossary)
        for i, j in _split(units, blocked):
            text = " ".join(u.text for u in units[i:j])
            cues.append(Cue(0, units[i].start, units[j - 1].end, wrap(text), segment.id))
    for n, cue in enumerate(cues):
        cue.index = n + 1
        last = n + 1 == len(cues)
        limit = cue.end + MIN_SECONDS + LINGER_SECONDS if last else cues[n + 1].start - GAP_SECONDS
        wanted = max(cue.end + LINGER_SECONDS, cue.start + MIN_SECONDS)
        cue.end = round(max(cue.end, min(wanted, limit, cue.start + MAX_SECONDS)), 3)
        cue.start = round(cue.start, 3)
    return cues


def timestamp(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def srt_text(cues: list[Cue]) -> str:
    blocks = [f"{c.index}\n{timestamp(c.start)} --> {timestamp(c.end)}\n" + "\n".join(c.lines) for c in cues]
    return "\n\n".join(blocks) + "\n"


@dataclass(frozen=True)
class SubtitleReport:
    cues: int
    reading: metrics.ReadingSpeedResult
    shorter_than_min: int
    longer_than_max: int
    lines_too_long: int


def report(cues: list[Cue]) -> SubtitleReport:
    return SubtitleReport(
        cues=len(cues),
        reading=metrics.reading_speed([(c.text, c.start, c.end) for c in cues]),
        shorter_than_min=sum(c.end - c.start < MIN_SECONDS - 1e-6 for c in cues),
        longer_than_max=sum(c.end - c.start > MAX_SECONDS + 1e-6 for c in cues),
        lines_too_long=sum(len(line) > MAX_LINE_CHARS for c in cues for line in c.lines),
    )


def write_srt(transcript: Transcript, out_path: Path, glossary: Glossary | None = None) -> SubtitleReport:
    """Write `transcript` as an SRT file (UTF-8); returns the report on its cues."""
    cues = build_cues(transcript, glossary)
    if not cues:
        raise ValueError(f"{transcript.source}: no text to write as subtitles")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(srt_text(cues), encoding="utf-8", newline="\n")
    return report(cues)
