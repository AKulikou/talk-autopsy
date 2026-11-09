"""
Subtitle files.

    <target_lang>.json -> <name>.<target_lang>.srt + <name>.<target_lang>.ass
    fixed.json         -> <name>.en.srt + <name>.en.ass (the corrected English)

Built in stage 2 as a viewing tool, ahead of stage 3, so the owner can watch
results. Owner decision of 2026-11-09 (decisions.md): no meaning is cut for
reading speed; cues hold up to three lines, every `.srt` gets a sibling `.ass`
with a smaller font so three lines fit, and reading speed is reported per file
(share of cues above 17 and above 25 characters per second), never enforced.

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
from dataclasses import dataclass
from pathlib import Path

from talk_autopsy import metrics
from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Transcript

MAX_LINE_CHARS = 48
MAX_LINES = 3
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
