"""
The speech recognition interface. Engines are pluggable: the benchmark compares
them on the same audio, and the rest of the pipeline sees only a Transcript.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable

from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Segment, Transcript


@runtime_checkable
class Transcriber(Protocol):
    """
    audio.wav (16 kHz mono, from ingest.extract_audio) -> raw.json (Transcript).

    `name` identifies the engine and its settings in Transcript.source and in
    results/benchmark.csv. An engine that accepts hints (a prompt or hotwords)
    takes them from the glossary; one that does not ignores it.
    """

    name: str

    def transcribe(self, audio_path: Path, glossary: Glossary | None) -> Transcript: ...


def drop_wordless(segments: list[Segment]) -> tuple[list[Segment], int]:
    """
    Remove segments whose text has no word (only `.` or other punctuation), then
    number the rest 1, 2, ... in order. Engines call it before writing the
    transcript, so ids are final before any later stage sees them. Returns the
    kept segments and how many were dropped.
    """
    kept = [s for s in segments if any(ch.isalnum() for ch in s.text)]
    renumbered = [s.model_copy(update={"id": number}) for number, s in enumerate(kept, start=1)]
    return renumbered, len(segments) - len(kept)
