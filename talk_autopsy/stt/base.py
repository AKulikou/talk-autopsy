"""
The speech recognition interface. Engines are pluggable: the benchmark compares
them on the same audio, and the rest of the pipeline sees only a Transcript.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable

from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Transcript


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
