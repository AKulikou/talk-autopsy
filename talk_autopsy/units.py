"""
Sentence units: what translation works on, and what the target-language
subtitles are cut from (since 2026-11-10, docs/experiments.md, D1 (setup)).

    fixed.json (segments) -> units: a Transcript whose segments are sentence units

Speech recognition segments do not always end where a sentence ends (the
Gemini engine often cuts mid-sentence). Translating half a sentence on its own
gave fragments: a subtitle that starts mid-sentence ("then this is your
chance...") or a cue ending on a comma (C1 blind, items 3 and 5). Units put the sentence back together before
translation:

- **Merge.** Consecutive segments join until the unit's text ends with
  terminal punctuation (. ? ! ...) or the pause after a segment is longer than
  MAX_PAUSE_SECONDS.
- **Split.** A unit longer than MAX_UNIT_SECONDS is split into the fewest
  parts that fit, each break at the best boundary near the even split point:
  a sentence end, else a clause end (, ; : dash), else a segment boundary,
  else any word.
- **Kept.** Every unit keeps its English words with their timings (`words`)
  and the ids of the segments it covers (`source_ids`; a segment split at a
  25-second limit appears in two units). A segment whose word list does not
  match its text (correction changed it and dropped the timings) gets word
  times shared out by characters over the segment.

Unit ids are 1..n in time order; they are not segment ids. Correction stays
segment-based; only translation and the target-language subtitles use units.
A segment with no text joins no unit.
"""

import re
from dataclasses import dataclass

from talk_autopsy.segments import Segment, Transcript, Word

VERSION = "sentence-units-v1"
MAX_PAUSE_SECONDS = 1.5
MAX_UNIT_SECONDS = 25.0

SENTENCE_END = re.compile(r"[.?!…][\"')\]»]*$")
CLAUSE_END = re.compile(r"([,;:]|—|–)[\"')\]»]*$|^[—–-]$")


def segment_words(segment: Segment) -> list[Word]:
    """
    The segment's words with times: its own word list when it matches the text
    word for word, else the text's words with the segment's time shared out by
    characters.
    """
    chunks = segment.text.split()
    words = segment.words or []
    if words and [w.text for w in words] == chunks:
        return [Word(start=w.start, end=max(w.end, w.start), text=w.text, probability=w.probability)
                for w in words]
    total = sum(len(c) + 1 for c in chunks) or 1
    span, t, out = segment.end - segment.start, segment.start, []
    for chunk in chunks:
        length = span * (len(chunk) + 1) / total
        out.append(Word(start=round(t, 3), end=round(t + length, 3), text=chunk))
        t += length
    return out


@dataclass
class _Token:
    word: Word
    segment: Segment
    first: bool  # the first word of its segment
    last: bool  # the last word of its segment


def _tokens(segment: Segment) -> list[_Token]:
    words = segment_words(segment)
    return [_Token(w, segment, n == 0, n == len(words) - 1) for n, w in enumerate(words)]


def _start(t: _Token) -> float:
    return t.segment.start if t.first else t.word.start


def _end(t: _Token) -> float:
    return t.segment.end if t.last else t.word.end


def _break_rank(tokens: list[_Token], k: int) -> int:
    """Rank of a break between tokens k-1 and k: 0 sentence end, 1 clause end, 2 segment boundary, 3 word."""
    text = tokens[k - 1].word.text
    if SENTENCE_END.search(text):
        return 0
    if CLAUSE_END.search(text):
        return 1
    if tokens[k].segment.id != tokens[k - 1].segment.id:
        return 2
    return 3


def split_long(tokens: list[_Token], max_seconds: float = MAX_UNIT_SECONDS) -> list[list[_Token]]:
    """
    `tokens` in parts of at most `max_seconds` (a single word longer than that
    stays one part). Each break is the best-ranked boundary that keeps the part
    within the limit, the one nearest the point that would split what is left
    into equal parts.
    """
    parts, i = [], 0
    while i < len(tokens):
        remaining = _end(tokens[-1]) - _start(tokens[i])
        if remaining <= max_seconds:
            parts.append(tokens[i:])
            break
        pieces = -(-remaining // max_seconds)  # ceil: the fewest parts that can fit
        target = _start(tokens[i]) + remaining / pieces
        fitting = [k for k in range(i + 1, len(tokens))
                   if _end(tokens[k - 1]) - _start(tokens[i]) <= max_seconds]
        if not fitting:
            fitting = [i + 1]
        best = min(_break_rank(tokens, k) for k in fitting)
        k = min((k for k in fitting if _break_rank(tokens, k) == best),
                key=lambda k: abs(_end(tokens[k - 1]) - target))
        parts.append(tokens[i:k])
        i = k
    return parts


def make_units(transcript: Transcript, max_pause: float = MAX_PAUSE_SECONDS,
               max_seconds: float = MAX_UNIT_SECONDS) -> Transcript:
    """The transcript's segments regrouped into sentence units (module docstring); meta notes the counts."""
    groups: list[list[_Token]] = []
    current: list[_Token] = []
    segments = transcript.segments
    for n, segment in enumerate(segments):
        current += _tokens(segment)
        following = segments[n + 1] if n + 1 < len(segments) else None
        ends_sentence = bool(SENTENCE_END.search(segment.text.strip()))
        pause = following is None or following.start - segment.end > max_pause
        if current and (ends_sentence or pause):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    parts = [part for group in groups for part in split_long(group, max_seconds)]
    units = []
    for number, part in enumerate(parts, start=1):
        ids = list(dict.fromkeys(t.segment.id for t in part))
        units.append(Segment(id=number, start=_start(part[0]), end=max(_end(part[-1]), _start(part[0])),
                             text=" ".join(t.word.text for t in part), words=[t.word for t in part],
                             source_ids=ids))
    covered = [i for u in units for i in u.source_ids or []]
    meta = dict(transcript.meta)
    meta["units"] = {
        "version": VERSION,
        "max_pause_seconds": max_pause,
        "max_unit_seconds": max_seconds,
        "segments": len(segments),
        "units": len(units),
        "merged_units": sum(len(u.source_ids or []) > 1 for u in units),
        "split_segments": len({i for i in covered if covered.count(i) > 1}),
    }
    return Transcript(segments=units, language=transcript.language, source=f"{transcript.source} + units",
                      meta=meta)
