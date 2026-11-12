"""
The waiting screen's text, built on the server from a job's events, so the page
only renders rows (no JavaScript logic to test; docs/web.md).

    transcript events (recognized text, all at once)  -> segments
    corrected events  (one per correction block)       -> corrected text + edits per segment
    translated events (one per translation block)      -> sentence units with their segment ids

A **row** (group) is the smallest run of consecutive segments that no sentence
unit crosses: one unit may span two segments (the sentence ends in the next
one), and one long segment may be split into two units. The left column shows
the row's segments, the right column its units' translations; before
translation every segment is its own row.

Correction edits are shown in place by a word diff of the recognized text
against the corrected text (`pieces`: same / del / ins), which also covers a
change the model did not list as an edit; the counter is the number of edits
the model listed.

`window(start, count)` returns the rows that touch segments `start ..
start+count-1` (whole rows), so the page keeps a bounded number of rows in the
DOM; indexes are segment positions, which stay stable when later translation
blocks merge segments into rows. Everything here is folded from events, so a
replayed event list gives the same view.

The summary names the time ranges of failed blocks (`untranslated`,
`uncorrected`), so the page can say in plain words which minutes were left as
they were. A segment's end comes with the recognized text (older recordings
have none: the next segment's start, else the segment's own start).
"""

import difflib
import threading
from dataclasses import dataclass, field

from talk_autopsy.web.notices import merge_ranges

TYPES = ("transcript", "corrected", "translated")
MAX_SEGMENTS = 6000  # about two hours of talk; more is dropped with a flag
MAX_WINDOW = 300


@dataclass
class _Segment:
    id: int
    start: float | None
    text: str
    corrected: str | None = None
    end: float | None = None
    failed: bool = False  # its correction block failed validation: the text stays as recognized


@dataclass
class _Unit:
    id: int
    text: str | None
    source: str
    source_ids: list[int] = field(default_factory=list)
    failed: bool = False


def pieces(before: str, after: str | None) -> list[dict]:
    """Word diff of `before` -> `after`: [{"k": "same" | "del" | "ins", "t": text}], neighbours merged."""
    if after is None or after == before:
        return [{"k": "same", "t": before}]
    a, b = before.split(), after.split()
    out: list[dict] = []

    def put(kind: str, words: list[str]) -> None:
        if not words:
            return
        if out and out[-1]["k"] == kind:
            out[-1]["t"] += " " + " ".join(words)
        else:
            out.append({"k": kind, "t": " ".join(words)})

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            put("same", a[i1:i2])
        else:
            put("del", a[i1:i2])
            put("ins", b[j1:j2])
    return out


class TranscriptView:
    """Folds transcript / corrected / translated events; thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._segments: list[_Segment] = []
        self._index: dict[int, int] = {}
        self._units: dict[int, _Unit] = {}
        self.version = 0
        self.focus = 0  # segment index of the newest change
        self.edits = 0
        self.correction_blocks = self.translation_blocks = 0
        self.failed_blocks = 0
        self.source = ""  # stt | stt2: which recognition the left column shows
        self.truncated = False

    # --- folding ---------------------------------------------------------------------

    def apply(self, event: dict) -> bool:
        """Fold one job event in; True if the view changed."""
        kind = event.get("type")
        if kind not in TYPES:
            return False
        with self._lock:
            if kind == "transcript":
                self._transcript(event)
            elif kind == "corrected":
                self._corrected(event)
            else:
                self._translated(event)
            self.version += 1
            return True

    def _add_segment(self, segment_id: int, start: float | None, text: str,
                     end: float | None = None) -> int | None:
        if segment_id in self._index:
            return self._index[segment_id]
        if len(self._segments) >= MAX_SEGMENTS:
            self.truncated = True
            return None
        self._index[segment_id] = len(self._segments)
        self._segments.append(_Segment(segment_id, start, text, end=end))
        return self._index[segment_id]

    def _transcript(self, event: dict) -> None:
        if event.get("replace"):  # the second recognition pass replaces the first: new segment ids
            self._segments, self._index, self._units = [], {}, {}
            self.edits = self.correction_blocks = self.translation_blocks = self.failed_blocks = 0
        self.source = str(event.get("source") or self.source)
        last = None
        for row in event.get("rows") or []:
            last = self._add_segment(int(row["id"]), row.get("start"), str(row.get("text") or ""),
                                     row.get("end"))
        if last is not None:
            self.focus = last

    def _corrected(self, event: dict) -> None:
        self.correction_blocks += 1
        if event.get("failed"):
            self.failed_blocks += 1
            for segment_id in event.get("ids") or []:
                index = self._index.get(int(segment_id))
                if index is not None:
                    self._segments[index].failed = True
                    self.focus = index
            return
        last = None
        for row in event.get("segments") or []:
            index = self._index.get(int(row["id"]))
            if index is None:  # a segment the page never saw recognized (should not happen): show it
                index = self._add_segment(int(row["id"]), None, str(row.get("text") or ""))
                if index is None:
                    continue
            self._segments[index].corrected = str(row.get("text") or "")
            last = index
        self.edits += len(event.get("edits") or [])
        if last is not None:
            self.focus = last

    def _translated(self, event: dict) -> None:
        self.translation_blocks += 1
        source_ids = event.get("source_ids") or {}
        if event.get("failed"):
            self.failed_blocks += 1
            for unit_id in event.get("ids") or []:
                ids = [int(i) for i in source_ids.get(str(unit_id), [])]
                self._units[int(unit_id)] = _Unit(int(unit_id), None, "", ids, failed=True)
            self._focus_on_units(event.get("ids") or [])
            return
        for row in event.get("units") or []:
            unit_id = int(row["id"])
            ids = [int(i) for i in row.get("source_ids") or source_ids.get(str(unit_id), [])]
            text, source = str(row.get("text") or ""), str(row.get("source") or "")
            self._units[unit_id] = _Unit(unit_id, text, source, ids)
        self._focus_on_units([row["id"] for row in event.get("units") or []])

    def _focus_on_units(self, unit_ids: list) -> None:
        units = [self._units[int(u)] for u in unit_ids if int(u) in self._units]
        indexes = [self._index[i] for unit in units for i in unit.source_ids if i in self._index]
        if indexes:
            self.focus = max(indexes)

    # --- reading ---------------------------------------------------------------------

    def _groups(self) -> list[tuple[int, int]]:
        """(first, last) segment indexes of every row, in order."""
        n = len(self._segments)
        reach = list(range(n))
        for unit in self._units.values():
            indexes = [self._index[i] for i in unit.source_ids if i in self._index]
            if indexes:
                lo, hi = min(indexes), max(indexes)
                reach[lo] = max(reach[lo], hi)
        groups, i = [], 0
        while i < n:
            end, j = reach[i], i
            while j <= end:
                end = max(end, reach[j])
                j += 1
            groups.append((i, end))
            i = end + 1
        return groups

    def summary(self) -> dict:
        with self._lock:
            return self._summary()

    def _span(self, index: int) -> tuple[float, float] | None:
        segment = self._segments[index]
        if segment.start is None:
            return None
        end = segment.end
        if end is None:
            following = self._segments[index + 1].start if index + 1 < len(self._segments) else None
            end = following if following is not None else segment.start
        return segment.start, end

    def _ranges(self, indexes) -> list[list[float]]:
        return merge_ranges([span for i in indexes if (span := self._span(i)) is not None])

    def _summary(self) -> dict:
        translated = sum(1 for u in self._units.values() if u.text is not None)
        untranslated = {self._index[i] for u in self._units.values() if u.failed
                        for i in u.source_ids if i in self._index}
        uncorrected = {k for k, s in enumerate(self._segments) if s.failed}
        return {"version": self.version, "segments": len(self._segments), "focus": self.focus,
                "edits": self.edits, "corrected": sum(1 for s in self._segments if s.corrected is not None),
                "units": translated, "correction_blocks": self.correction_blocks,
                "translation_blocks": self.translation_blocks, "failed_blocks": self.failed_blocks,
                "source": self.source, "truncated": self.truncated,
                "untranslated": self._ranges(sorted(untranslated)),
                "uncorrected": self._ranges(sorted(uncorrected))}

    def window(self, start: int | None = None, count: int = 120, follow: bool = False) -> dict:
        """
        Whole rows touching segment indexes start .. start+count-1. With `follow`,
        the window ends a little after the newest change.
        """
        count = max(1, min(int(count), MAX_WINDOW))
        with self._lock:
            n = len(self._segments)
            if follow or start is None:
                start = self.focus - count + max(1, count // 5)
            start = max(0, min(int(start), max(0, n - 1)))
            stop = min(n, start + count)
            units_by_first: dict[int, list[_Unit]] = {}
            for unit in sorted(self._units.values(), key=lambda u: u.id):
                indexes = [self._index[i] for i in unit.source_ids if i in self._index]
                if indexes:
                    units_by_first.setdefault(min(indexes), []).append(unit)
            rows = []
            for first, last in self._groups():
                if last < start or first >= stop:
                    continue
                segments = self._segments[first:last + 1]
                units = [u for i in range(first, last + 1) for u in units_by_first.get(i, [])]
                rows.append({
                    "first": first, "last": last,
                    "start": next((s.start for s in segments if s.start is not None), None),
                    "segments": [{"id": s.id, "pieces": pieces(s.text, s.corrected),
                                  "changed": s.corrected is not None and s.corrected != s.text,
                                  "failed": s.failed} for s in segments],
                    "units": [{"id": u.id, "text": u.text, "failed": u.failed} for u in units],
                    "newest": first <= self.focus <= last,
                })
            first_shown = rows[0]["first"] if rows else start
            last_shown = rows[-1]["last"] if rows else start - 1
            return {"summary": self._summary(), "start": first_shown, "end": last_shown + 1, "rows": rows,
                    "more_before": first_shown > 0, "more_after": last_shown + 1 < n}
