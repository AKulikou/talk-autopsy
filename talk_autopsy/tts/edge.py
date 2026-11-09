"""
Dubbing with edge-tts voices, mixed with ffmpeg.

    <target_lang>.json + audio.wav -> <name>.<target_lang>.dub.m4a

Each segment is spoken in the target language and fitted to its time slot; the
original audio is kept quietly underneath. Stage 4.
"""

from pathlib import Path

from talk_autopsy.segments import Transcript


def dub(transcript: Transcript, target_lang: str, original_audio: Path, out_audio: Path) -> Path:
    raise NotImplementedError("stage 4")
