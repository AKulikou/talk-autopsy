"""
Subtitle files.

    <target_lang>.json -> <name>.<target_lang>.srt + <name>.<target_lang>.ass
    fixed.json         -> <name>.en.srt + <name>.en.ass (the corrected English)

Built in stage 2 as a viewing tool, ahead of stage 3, so the owner can watch
results. Owner decision of 2026-11-09 (decisions.md): no meaning is cut for
reading speed; cues hold up to three lines, every `.srt` gets a sibling `.ass`
with a smaller font so three lines fit, and reading speed is reported per file
(share of cues above 17 and above 25 characters per second), never enforced.

Cues never cross a segment boundary. The English file is cut from the corrected
segments; the target-language file from the translation's segments, which are
sentence units (units.py) since 2026-11-10. Inside a segment:

- **Timing.** Three cases. (1) The segment's words are its text's words (the
  English): a cue runs from its first word's start to its last word's end.
  (2) The segment carries other words than its text (a translated unit keeps
  its English words): a cue covering target characters [a, b) of the text
  covers the same share of the English words, and gets their times, so pauses
  between English words become pauses between cues. A break that ends a target
  sentence (clause) moves to an English sentence (clause) end when one lies
  within SNAP_WORDS words. (3) No words (a translation made per segment): the
  segment's time is shared out by character count.
- **Splitting.** A cue holds at most MAX_LINES lines of MAX_LINE_CHARS
  characters and lasts at most MAX_SECONDS. Among the break points that keep a
  cue within both limits, the latest sentence end (. ? !) is taken, else the
  latest clause end (, ; : and dashes), else the latest word boundary; a break
  is never placed inside a glossary term of several words.
- **Display time.** After cutting, each cue's end is extended into the silence
  that follows: by up to EXTEND_SECONDS, never closer than GAP_SECONDS to the
  next cue's start and never past MAX_SECONDS on screen. Cues never overlap.
  Cues outside 1–7 s are counted in the report.
- **Lines.** A cue longer than one line is wrapped into the fewest lines (up
  to MAX_LINES) that keep every line within MAX_LINE_CHARS, at the spaces that
  balance the lines best (a break after punctuation wins a tie).
- **.ass.** One style: Arial 42 on a 1920x1080 canvas, white text with a dark
  outline and a light shadow, bottom centre, a bottom margin that keeps three
  lines above the edge; line breaks as in the `.srt`, no effects.

`report` gives the reading speed per cue (metrics.reading_speed), the shares
of cues above 17 and 25 characters per second, and the cues outside the limits.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from talk_autopsy import metrics
from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Segment, Transcript, Word
from talk_autopsy.units import CLAUSE_END as _CLAUSE_END
from talk_autopsy.units import SENTENCE_END as _SENTENCE_END
from talk_autopsy.units import segment_words

MAX_LINE_CHARS = 48
MAX_LINES = 3
MIN_SECONDS = 1.0
MAX_SECONDS = 7.0
EXTEND_SECONDS = 1.5  # display time added after a cue's last word, into the silence that follows
GAP_SECONDS = 0.1  # the smallest gap an extended cue keeps before the next cue
SNAP_WORDS = 3  # how far (in English words) a sentence or clause break may move to meet the English one


@dataclass
class Unit:
    """One word of a segment's text with its time."""

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


class _WordTimes:
    """Cases (1) and (3): every word of the text has its own time."""

    def __init__(self, units: list[Unit]):
        self.units = units

    def span(self, i: int, j: int) -> tuple[float, float]:
        return self.units[i].start, self.units[j - 1].end

    def final(self, ranges: list[tuple[int, int]]) -> list[tuple[float, float]]:
        return [self.span(i, j) for i, j in ranges]


class _SourceTimes:
    """
    Case (2): the text's words are timed through the segment's other words (a
    translated unit's English words). The break before text word k sits before
    English word `position(k)`: the same share of the English words as of the
    text's characters, moved to a matching English sentence or clause end
    within SNAP_WORDS words.
    """

    def __init__(self, segment: Segment, chunks: list[str], source: list[Word], fallback: _WordTimes):
        self.segment, self.chunks, self.source, self.fallback = segment, chunks, source, fallback
        self.offsets = [0]
        for chunk in chunks:
            self.offsets.append(self.offsets[-1] + len(chunk) + 1)

    def position(self, k: int) -> int:
        n = len(self.source)
        if k <= 0:
            return 0
        if k >= len(self.chunks):
            return n
        raw = self.offsets[k] / self.offsets[-1] * n
        base = min(max(round(raw), 1), n - 1)
        before = self.chunks[k - 1]
        if _SENTENCE_END.search(before):
            patterns = [_SENTENCE_END]
        elif _CLAUSE_END.search(before):
            patterns = [_SENTENCE_END, _CLAUSE_END]
        else:
            return base
        near = [q for q in range(base - SNAP_WORDS, base + SNAP_WORDS + 1)
                if 1 <= q <= n - 1 and any(p.search(self.source[q - 1].text) for p in patterns)]
        return min(near, key=lambda q: (abs(q - raw), q)) if near else base

    def _times(self, a: int, b: int) -> tuple[float, float]:
        """From English word a to English word b - 1; the segment's own edges at the ends."""
        n = len(self.source)
        start = self.segment.start if a == 0 else self.source[a].start
        end = self.segment.end if b == n else self.source[b - 1].end
        return start, max(start, end)

    def span(self, i: int, j: int) -> tuple[float, float]:
        a = min(self.position(i), len(self.source) - 1)
        return self._times(a, max(self.position(j), a + 1))

    def final(self, ranges: list[tuple[int, int]]) -> list[tuple[float, float]]:
        """Each cue gets at least one English word; with fewer English words than cues, by characters."""
        n = len(self.source)
        if n < len(ranges):
            return self.fallback.final(ranges)
        cuts = [self.position(i) for i, _ in ranges] + [n]
        for m in range(1, len(cuts) - 1):
            cuts[m] = max(cuts[m], cuts[m - 1] + 1)
        for m in range(len(cuts) - 2, 0, -1):
            cuts[m] = min(cuts[m], cuts[m + 1] - 1)
        return [self._times(a, b) for a, b in zip(cuts, cuts[1:], strict=False)]


def _timing(segment: Segment, chunks: list[str]) -> _WordTimes | _SourceTimes:
    words = segment.words or []
    if words and [w.text for w in words] == chunks:
        return _WordTimes([Unit(w.text, w.start, max(w.end, w.start)) for w in words])
    by_characters = _WordTimes([Unit(w.text, w.start, w.end) for w in segment_words(
        Segment(id=segment.id, start=segment.start, end=segment.end, text=segment.text))])
    if len(words) >= 2:
        return _SourceTimes(segment, chunks, sorted(words, key=lambda w: w.start), by_characters)
    return by_characters


def _protected(chunks: list[str], glossary: Glossary | None) -> set[int]:
    """Break positions (a break at k = between word k-1 and word k) inside a multi-word glossary term."""
    if glossary is None:
        return set()
    words = [re.sub(r"^\W+|\W+$", "", c).casefold() for c in chunks]
    blocked: set[int] = set()
    for entry in glossary.entries:
        parts = [p.casefold() for p in entry.term.split()]
        if len(parts) < 2:
            continue
        for i in range(len(words) - len(parts) + 1):
            if words[i:i + len(parts) - 1] == parts[:-1] and words[i + len(parts) - 1].startswith(parts[-1]):
                blocked.update(range(i + 1, i + len(parts)))
    return blocked


def _breaks(spaces: list[int], count: int) -> list[tuple[int, ...]]:
    """Every choice of `count` break positions among `spaces`, in order."""
    if count == 0:
        return [()]
    out = []
    for k, space in enumerate(spaces):
        out += [(space, *rest) for rest in _breaks(spaces[k + 1:], count - 1)]
    return out


def wrap(text: str, max_chars: int = MAX_LINE_CHARS, max_lines: int = MAX_LINES) -> list[str]:
    """
    One line if it fits, else the fewest lines (up to `max_lines`) whose longest
    line is shortest, split at spaces; among equally long layouts the one with
    more breaks after punctuation wins. If no layout fits, the most balanced
    layout with `max_lines` lines is returned (the caller checks `_fits`).
    """
    if len(text) <= max_chars:
        return [text]
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    if not spaces:
        return [text]
    best: list[str] = [text]
    for count in range(1, min(max_lines, len(spaces) + 1)):
        candidates = []
        for cut in _breaks(spaces, count):
            bounds = [-1, *cut, len(text)]
            lines = [text[a + 1:b] for a, b in zip(bounds, bounds[1:], strict=False)]
            punctuated = sum(text[c - 1] in ",;:.?!" for c in cut)
            candidates.append((max(len(line) for line in lines), -punctuated, lines))
        longest, _, lines = min(candidates, key=lambda c: (c[0], c[1]))
        best = lines
        if longest <= max_chars:
            return lines
    return best


def _fits(text: str) -> bool:
    if len(text) > MAX_LINES * (MAX_LINE_CHARS + 1):  # cannot fit however it is wrapped
        return False
    lines = wrap(text)
    return len(lines) <= MAX_LINES and all(len(line) <= MAX_LINE_CHARS for line in lines)


def _split(chunks: list[str], blocked: set[int],
           span: Callable[[int, int], tuple[float, float]]) -> list[tuple[int, int]]:
    """[(i, j)] word ranges, one per cue; `span(i, j)` is the time of words i..j-1."""
    ranges, i = [], 0
    while i < len(chunks):
        candidates = []
        for j in range(i + 1, len(chunks) + 1):
            start, end = span(i, j)
            if j > i + 1 and (not _fits(" ".join(chunks[i:j])) or end - start > MAX_SECONDS):
                break
            if j == len(chunks) or j not in blocked:
                candidates.append(j)
        if not candidates:  # a term longer than a cue: break inside it rather than nowhere
            candidates = [i + 1]
        if candidates[-1] == len(chunks):
            ranges.append((i, len(chunks)))
            break
        sentence = [j for j in candidates if _SENTENCE_END.search(chunks[j - 1])]
        clause = [j for j in candidates if _CLAUSE_END.search(chunks[j - 1])]
        end = (sentence or clause or candidates)[-1]
        ranges.append((i, end))
        i = end
    return ranges


def extend_display(cues: list[Cue]) -> None:
    """
    Each cue's end moves into the silence after it: by up to EXTEND_SECONDS, to
    at most GAP_SECONDS before the next cue and MAX_SECONDS on screen; a cue
    that already reaches the next one is cut back to its start, so no two cues
    overlap. Numbers the cues and rounds times to milliseconds.
    """
    for cue in cues:
        cue.start = round(cue.start, 3)
    for n, cue in enumerate(cues):
        cue.index = n + 1
        following = cues[n + 1] if n + 1 < len(cues) else None
        limit = following.start - GAP_SECONDS if following else float("inf")
        end = max(cue.end, min(cue.end + EXTEND_SECONDS, limit, cue.start + MAX_SECONDS))
        if following is not None:
            end = min(end, following.start)
        cue.end = round(max(end, cue.start), 3)


def build_cues(transcript: Transcript, glossary: Glossary | None = None) -> list[Cue]:
    cues: list[Cue] = []
    for segment in transcript.segments:
        chunks = segment.text.split()
        if not chunks:
            continue
        timing = _timing(segment, chunks)
        ranges = _split(chunks, _protected(chunks, glossary), timing.span)
        for (i, j), (start, end) in zip(ranges, timing.final(ranges), strict=True):
            cues.append(Cue(0, start, end, wrap(" ".join(chunks[i:j])), segment.id))
    extend_display(cues)
    return cues


def timestamp(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def srt_text(cues: list[Cue]) -> str:
    blocks = [f"{c.index}\n{timestamp(c.start)} --> {timestamp(c.end)}\n" + "\n".join(c.lines) for c in cues]
    return "\n\n".join(blocks) + "\n"


FAST_CPS = 17.0  # common subtitle guideline for adult viewers
VERY_FAST_CPS = 25.0  # hard to read even for a fast reader


@dataclass(frozen=True)
class SubtitleReport:
    cues: int
    reading: metrics.ReadingSpeedResult
    shorter_than_min: int
    longer_than_max: int
    lines_too_long: int  # lines over MAX_LINE_CHARS plus cues over MAX_LINES
    share_above_17: float
    share_above_25: float

    def line(self) -> str:
        return (f"{self.cues} cues; reading speed max {self.reading.max_cps:.1f} cps, "
                f"{self.share_above_17 * 100:.1f}% above {FAST_CPS:.0f} cps, "
                f"{self.share_above_25 * 100:.1f}% above {VERY_FAST_CPS:.0f} cps; shorter than "
                f"{MIN_SECONDS:.0f} s: {self.shorter_than_min}, longer than {MAX_SECONDS:.0f} s: "
                f"{self.longer_than_max}")

    def as_dict(self) -> dict:
        return {"cues": self.cues, "max_cps": round(self.reading.max_cps, 1),
                "share_above_17_cps": round(self.share_above_17, 4),
                "share_above_25_cps": round(self.share_above_25, 4),
                "shorter_than_min": self.shorter_than_min, "longer_than_max": self.longer_than_max,
                "lines_too_long": self.lines_too_long}


def report(cues: list[Cue]) -> SubtitleReport:
    reading = metrics.reading_speed([(c.text, c.start, c.end) for c in cues], threshold=FAST_CPS)
    return SubtitleReport(
        cues=len(cues),
        reading=reading,
        shorter_than_min=sum(c.end - c.start < MIN_SECONDS - 1e-6 for c in cues),
        longer_than_max=sum(c.end - c.start > MAX_SECONDS + 1e-6 for c in cues),
        lines_too_long=sum(len(line) > MAX_LINE_CHARS for c in cues for line in c.lines)
        + sum(len(c.lines) > MAX_LINES for c in cues),
        share_above_17=sum(cps > FAST_CPS for cps in reading.cps) / len(cues),
        share_above_25=sum(cps > VERY_FAST_CPS for cps in reading.cps) / len(cues),
    )


# --- .ass ---------------------------------------------------------------------------

ASS_PLAY_RES = (1920, 1080)
ASS_FONT = "Arial"  # present on every Windows installation; libass substitutes a sans-serif elsewhere
ASS_FONT_SIZE = 42
ASS_MARGIN_V = 60  # px between the bottom edge and the last line
ASS_MARGIN_H = 120
ASS_STYLE_FORMAT = ("Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
                    "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
                    "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
ASS_EVENT_FORMAT = "Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
_BACKSLASH = chr(92)
ASS_LINE_BREAK = _BACKSLASH + "N"


def ass_timestamp(seconds: float) -> str:
    """H:MM:SS.cc, the time format of .ass files (centiseconds)."""
    cs = max(0, round(seconds * 100))
    return f"{cs // 360_000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def ass_escape(line: str) -> str:
    """Literal text for an .ass event: a backslash or a brace would start an override code."""
    return (line.replace(_BACKSLASH, _BACKSLASH * 2).replace("{", _BACKSLASH + "{")
            .replace("}", _BACKSLASH + "}"))


def ass_text(cues: list[Cue], title: str = "") -> str:
    """The cues as an .ass file: one style, the cue's own line breaks, no effects."""
    width, height = ASS_PLAY_RES
    style = ",".join(str(v) for v in (
        "Default", ASS_FONT, ASS_FONT_SIZE,
        "&H00FFFFFF", "&H000000FF", "&H00101010", "&H80000000",  # white text, near-black outline, shadow
        0, 0, 0, 0, 100, 100, 0, 0,
        1, 2.5, 1,  # border style 1 (outline + shadow), outline 2.5 px, shadow 1 px at PlayRes
        2,  # alignment: bottom centre
        ASS_MARGIN_H, ASS_MARGIN_H, ASS_MARGIN_V, 1))
    lines = [
        "[Script Info]",
        "; written by talk-autopsy",
        f"Title: {title or 'subtitles'}",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",  # no automatic wrapping: the lines are laid out already
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        f"Format: {ASS_STYLE_FORMAT}",
        f"Style: {style}",
        "",
        "[Events]",
        f"Format: {ASS_EVENT_FORMAT}",
    ]
    for c in cues:
        text = ASS_LINE_BREAK.join(ass_escape(line) for line in c.lines)
        lines.append(f"Dialogue: 0,{ass_timestamp(c.start)},{ass_timestamp(c.end)},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


def write_srt(transcript: Transcript, out_path: Path, glossary: Glossary | None = None,
              ass: bool = True) -> SubtitleReport:
    """
    Write `transcript` as an SRT file (UTF-8) and, with `ass`, the sibling `.ass`
    file with the same cues; returns the report on the cues.
    """
    cues = build_cues(transcript, glossary)
    if not cues:
        raise ValueError(f"{transcript.source}: no text to write as subtitles")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(srt_text(cues), encoding="utf-8", newline="\n")
    if ass:
        # with a byte-order mark, players such as VLC take the .ass file as UTF-8 for certain
        out_path.with_suffix(".ass").write_text(ass_text(cues, out_path.stem), encoding="utf-8-sig",
                                                newline="\n")
    return report(cues)
