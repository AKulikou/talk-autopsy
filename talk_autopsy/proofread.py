"""
Proofreading round trip: a draft the owner corrects while listening, and the
reference transcript made from it.

    raw.json --draft_text--> T<n>.draft.txt --(owner edits)--> --reference_from_draft--> T<n>.ref.txt

Draft format: one segment per line as `[mm:ss] text` (the segment start), and a
blank line whenever the segment start enters a new minute of audio, so the owner
can find a place in the player. Minutes are not wrapped into hours: `[75:03]`.

Both files stay local (data/test-set/ ignores everything except the README and
the glossaries).
"""

import re

from talk_autopsy.segments import Transcript

TIMESTAMP = re.compile(r"^\s*\[\d{1,3}:\d{2}\]\s?")
BLOCK_SECONDS = 60


def timestamp(seconds: float) -> str:
    whole = int(seconds)
    return f"[{whole // 60:02d}:{whole % 60:02d}]"


def draft_text(transcript: Transcript) -> str:
    """The draft for proofreading: `[mm:ss] text` per segment, a blank line between minutes of audio."""
    lines: list[str] = []
    block = None
    for segment in transcript.segments:
        current = int(segment.start // BLOCK_SECONDS)
        if block is not None and current != block:
            lines.append("")
        block = current
        lines.append(f"{timestamp(segment.start)} {' '.join(segment.text.split())}")
    return "\n".join(lines) + "\n" if lines else ""


def reference_from_draft(draft: str) -> str:
    """
    The reference transcript from a proofread draft: `[mm:ss]` prefixes removed,
    the lines between blank lines joined into one paragraph, whitespace collapsed,
    paragraphs separated by one blank line. Everything else (including markers
    such as `[inaudible]`) is kept as written.
    """
    paragraphs: list[str] = []
    current: list[str] = []
    for line in draft.splitlines():
        text = " ".join(TIMESTAMP.sub("", line, count=1).split())
        if text:
            current.append(text)
        elif not line.strip() and current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs) + "\n" if paragraphs else ""
