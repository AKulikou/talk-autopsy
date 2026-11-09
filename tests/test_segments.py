"""The shared transcript format: validation, JSON round trip, stable ids."""

import pytest
from pydantic import ValidationError

from talk_autopsy.segments import (
    Segment,
    Transcript,
    Word,
    assert_same_ids,
    load_transcript,
    save_transcript,
)


def transcript(texts=("Hello, world.", "Claude Code runs in a terminal."), source="faster-whisper large-v3"):
    return Transcript(
        segments=[
            Segment(id=i, start=i * 2.0, end=i * 2.0 + 1.5, text=text,
                    words=[Word(start=i * 2.0, end=i * 2.0 + 0.4, text=text.split()[0], probability=0.9)],
                    confidence=-0.2)
            for i, text in enumerate(texts)
        ],
        language="en",
        source=source,
        meta={"input": "T1.wav"},
    )


def test_round_trip_keeps_everything(tmp_path):
    original = transcript()
    path = save_transcript(original, tmp_path / "run" / "raw.json")
    assert load_transcript(path) == original


def test_non_latin_text_is_stored_readable(tmp_path):
    translated = transcript(texts=("Привет, мир.",), source="translation")
    path = save_transcript(translated, tmp_path / "ru.json")
    assert "Привет, мир." in path.read_text(encoding="utf-8")
    assert load_transcript(path) == translated


def test_optional_fields_may_be_missing():
    segment = Segment.model_validate({"id": 0, "start": 0, "end": 1, "text": "hi"})
    assert segment.words is None and segment.confidence is None


def test_end_before_start_is_rejected():
    with pytest.raises(ValidationError, match="before start"):
        Segment(id=0, start=2.0, end=1.0, text="x")


def test_duplicate_ids_are_rejected():
    segments = [Segment(id=1, start=0, end=1, text="a"), Segment(id=1, start=1, end=2, text="b")]
    with pytest.raises(ValidationError, match="duplicate segment ids"):
        Transcript(segments=segments, language="en", source="test")


def test_same_ids_pass():
    corrected = transcript(texts=("Hello, World.", "Claude Code runs."), source="correction")
    assert_same_ids(transcript(), corrected)


@pytest.mark.parametrize("texts", [("only one",), ("a", "b", "c")])
def test_dropped_or_added_segments_are_caught(texts):
    with pytest.raises(ValueError, match="segment ids differ"):
        assert_same_ids(transcript(), transcript(texts=texts, source="correction"))


def test_renumbered_segments_are_caught():
    result = transcript(source="correction")
    result.segments[0].id, result.segments[1].id = 1, 0
    with pytest.raises(ValueError, match="order changed"):
        assert_same_ids(transcript(), result)
