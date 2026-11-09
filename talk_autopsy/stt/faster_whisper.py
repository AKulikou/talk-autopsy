"""
The first engine: faster-whisper (CTranslate2 Whisper) running locally on the CPU.

    audio.wav -> raw.json (Transcript with word timings and probabilities)

The model loads on first use, not at import: it is large and slow to load.
Stage 1.
"""

from pathlib import Path

from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Transcript


class FasterWhisperTranscriber:
    """Transcriber backed by faster-whisper; large-v3 on CPU with int8 weights by default."""

    def __init__(self, model_size: str = "large-v3", device: str = "cpu", compute_type: str = "int8"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.name = f"faster-whisper {model_size}"

    def transcribe(self, audio_path: Path, glossary: Glossary | None) -> Transcript:
        raise NotImplementedError("stage 1")
