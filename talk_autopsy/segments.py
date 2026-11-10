"""
The one data format every stage reads and writes.

A Transcript is a list of timed segments. Speech recognition creates the
segments; correction changes only their text and returns the same ids, so the
recognition and correction files of one run can be compared segment by segment
(assert_same_ids). Translation is the one exception: it works on sentence units
(units.py), whose segments are regrouped recognition segments with new ids, the
English words and their timings (`words`) and the covered segment ids
(`source_ids`).
"""

import json
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, model_validator


class Word(BaseModel):
    """One recognized word with its timing; probability is the engine's own score, if it gives one."""

    start: float
    end: float
    text: str
    probability: float | None = None


class Segment(BaseModel):
    """A timed piece of speech, usually a sentence or a phrase."""

    id: int
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str
    words: list[Word] | None = None
    confidence: float | None = None
    # only in a sentence unit (units.py): the ids of the recognition segments it covers
    source_ids: list[int] | None = None

    @model_validator(mode="after")
    def _end_not_before_start(self) -> Self:
        if self.end < self.start:
            raise ValueError(f"segment {self.id}: end {self.end} is before start {self.start}")
        return self


class Transcript(BaseModel):
    """
    segments: in time order, ids unique.
    language: ISO 639 code of the text in the segments.
    source: the engine or stage that wrote it ("faster-whisper large-v3", "correction", ...).
    meta: free-form run details (input file, model, prompt version, timings).
    """

    segments: list[Segment]
    language: str
    source: str
    meta: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [segment.id for segment in self.segments]
        if len(ids) != len(set(ids)):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate segment ids: {duplicates}")
        return self

    def ids(self) -> list[int]:
        return [segment.id for segment in self.segments]


def assert_same_ids(original: Transcript, result: Transcript) -> None:
    """Raise ValueError if a stage dropped, added or renumbered segments."""
    if original.ids() != result.ids():
        missing = sorted(set(original.ids()) - set(result.ids()))
        added = sorted(set(result.ids()) - set(original.ids()))
        raise ValueError(
            f"{result.source}: segment ids differ from {original.source} "
            f"(missing {missing}, added {added}, or the order changed)"
        )


def load_transcript(path: Path) -> Transcript:
    return Transcript.model_validate_json(Path(path).read_text(encoding="utf-8"))


def save_transcript(transcript: Transcript, path: Path) -> Path:
    """Write UTF-8 JSON with the text readable as is (no \\u escapes)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = transcript.model_dump(mode="json")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
