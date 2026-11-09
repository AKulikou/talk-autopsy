"""
Claude correction of the raw transcript.

    raw.json (Transcript from an STT engine) + glossary + video context -> fixed.json

Fixes technical terms, names and words distorted by the speaker's accent; leaves
everything else alone (the false-edit rate is measured). Returns the same segment
ids (segments.assert_same_ids). Stage 2.
"""

from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Transcript


def correct(transcript: Transcript, glossary: Glossary, context: str, *, model: str) -> Transcript:
    raise NotImplementedError("stage 2")
