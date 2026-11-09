"""
Claude translation of the corrected transcript.

    fixed.json + glossary -> <target_lang>.json

The transcript is translated in blocks of 5-10 minutes that overlap, so a sentence
cut at a block edge keeps its context; one glossary serves the whole video, so a
term reads the same in every block. target_lang is always an argument: no
language is assumed. Returns the same segment ids. Stage 2.
"""

from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Transcript


def translate(transcript: Transcript, glossary: Glossary, target_lang: str, *, model: str) -> Transcript:
    raise NotImplementedError("stage 2")
