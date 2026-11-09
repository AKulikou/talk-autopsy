"""
Subtitle files.

    <target_lang>.json -> <name>.<target_lang>.srt
    fixed.json         -> <name>.en.srt (the corrected English)

Line length and reading speed (characters per second) are checked, since a
translation is often longer than the original. Stage 3.
"""

from pathlib import Path

from talk_autopsy.segments import Transcript


def write_srt(transcript: Transcript, out_path: Path) -> Path:
    raise NotImplementedError("stage 3")
