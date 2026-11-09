"""Stages that arrive later are explicit stubs."""

from pathlib import Path

import pytest

from talk_autopsy import correct, subtitles, translate
from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Transcript
from talk_autopsy.tts import edge

EMPTY = Transcript(segments=[], language="en", source="test")


@pytest.mark.parametrize("call, stage", [
    (lambda: correct.correct(EMPTY, Glossary(), "context", model="m"), "stage 2"),
    (lambda: translate.translate(EMPTY, Glossary(), "de", model="m"), "stage 2"),
    (lambda: subtitles.write_srt(EMPTY, Path("x.srt")), "stage 3"),
    (lambda: edge.dub(EMPTY, "de", Path("a.wav"), Path("d.m4a")), "stage 4"),
])
def test_later_stages_say_which_stage_brings_them(call, stage):
    with pytest.raises(NotImplementedError, match=stage):
        call()
