"""Stages that arrive later are explicit stubs."""

from pathlib import Path

import pytest

from talk_autopsy.segments import Transcript
from talk_autopsy.tts import edge

EMPTY = Transcript(segments=[], language="en", source="test")


def test_dubbing_says_which_stage_brings_it():
    with pytest.raises(NotImplementedError, match="stage 4"):
        edge.dub(EMPTY, "de", Path("a.wav"), Path("d.m4a"))
