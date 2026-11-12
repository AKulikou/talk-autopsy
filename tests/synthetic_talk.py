"""
An invented technical talk for tests: about 12 minutes of segments with word
timings, invented product names and a glossary. No sentence comes from a real
video. Used by the golden request tests (Part 1 of the v1.1 web work) and the
fake model answers they need.

The fake answers are deterministic functions of the request text:
- correction fixes "core vex" -> "Quorvex" and "flinter mash" -> "flinter mesh",
  listing each fix as an edit;
- translation answers "<n>. перевод: ..." in Cyrillic, keeping "Quorvex" and
  using the glossary's agreed rendering of "flinter mesh".
"""

import re

from talk_autopsy.correct import CorrectionAnswer, Edit
from talk_autopsy.correct import SegmentText as FixSegment
from talk_autopsy.segments import Segment, Transcript, Word
from talk_autopsy.translate import SegmentText as UnitText
from talk_autopsy.translate import TranslationAnswer
from tests.conftest import response

# Invented sentences; some end with a comma, so a sentence unit spans two segments.
SENTENCES = [
    "Welcome everyone, today we look at how core vex schedules small jobs.",
    "The first idea is simple: every worker pulls from one Brindle queue,",
    "and the queue decides who gets the next piece of work.",
    "We tried a flinter mash between the workers last spring.",
    "It looked clever on paper but it doubled the latency.",
    "So we went back to the plain queue and measured again.",
    "Core vex now keeps a small buffer per worker,",
    "which hides most of the network jitter we saw before.",
    "Let me show the numbers from the test cluster.",
    "On a quiet day the median job waits about forty milliseconds.",
    "On a busy day it is closer to two hundred.",
    "The flinter mesh idea is not dead, though.",
    "We think it helps when the workers sit in different regions.",
    "That is what the next release of Quorvex will try.",
]
SEGMENT_SECONDS = 8.5
SEGMENT_COUNT = 84  # about 12 minutes: three correction blocks

GLOSSARY_MD = """<!-- glossary of this run: invented terms for the golden tests -->

| term | keep | ru | notes |
|---|---|---|---|
| Quorvex | yes | | invented product name |
| Brindle queue | yes | | invented component |
| flinter mesh | no | сетка флинтера | invented networking idea |
"""

FIXES = {"core vex": "Quorvex", "Core vex": "Quorvex", "flinter mash": "flinter mesh"}


def segments() -> list[Segment]:
    out = []
    for n in range(1, SEGMENT_COUNT + 1):
        text = SENTENCES[(n - 1) % len(SENTENCES)]
        start = round((n - 1) * SEGMENT_SECONDS, 2)
        end = round(start + SEGMENT_SECONDS - 0.5, 2)
        tokens = text.split()
        step = (end - start) / len(tokens)
        words = [Word(start=round(start + k * step, 3), end=round(start + (k + 1) * step - 0.05, 3), text=w)
                 for k, w in enumerate(tokens)]
        out.append(Segment(id=n, start=start, end=end, text=text, words=words, confidence=0.9))
    return out


def transcript() -> Transcript:
    return Transcript(segments=segments(), language="en", source="synthetic assemblyai",
                      meta={"engine": "synthetic", "timestamps": "measured", "cost_usd": 0.0,
                            "audio_seconds": SEGMENT_COUNT * SEGMENT_SECONDS, "wall_seconds": 1.0})


def requested(user: str, verb: str) -> list[tuple[int, str]]:
    body = user.split(f"Segments to {verb}:")[1]
    return [(int(i), text) for i, text in re.findall(r"^\[(\d+)\] (.*)$", body, re.M)]


def fixed_text(text: str) -> tuple[str, list[tuple[str, str]]]:
    edits = []
    for before, after in FIXES.items():
        if before in text:
            text = text.replace(before, after)
            edits.append((before, after))
    return text, edits


def translated_text(number: int, text: str) -> str:
    parts = [f"{number}. перевод фразы из {len(text.split())} слов"]
    if "Quorvex" in text:
        parts.append("про Quorvex")
    if "Brindle queue" in text:
        parts.append("и Brindle queue")
    if "flinter mesh" in text:
        parts.append("и сетка флинтера")
    return " ".join(parts) + "."


def handler(kwargs):
    """The fake model: a correction or a translation answer for the request's segments."""
    user = kwargs["messages"][0]["content"]
    if kwargs["output_format"] is CorrectionAnswer:
        segs, edits = [], []
        for i, text in requested(user, "correct"):
            new, made = fixed_text(text)
            segs.append(FixSegment(id=i, text=new))
            edits += [Edit(id=i, before=b, after=a, reason="glossary term") for b, a in made]
        return response(CorrectionAnswer(segments=segs, edits=edits), input_tokens=900 + len(user) // 4,
                        output_tokens=400 + len(user) // 5)
    if kwargs["output_format"] is TranslationAnswer:
        segs = [UnitText(id=i, text=translated_text(i, text)) for i, text in requested(user, "translate")]
        return response(TranslationAnswer(segments=segs), input_tokens=800 + len(user) // 4,
                        output_tokens=500 + len(user) // 4)
    raise AssertionError(f"unexpected output format {kwargs['output_format']!r}")
