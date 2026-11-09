"""Forced alignment with a stub acoustic model: no torch, no model download."""

import math
import wave

import numpy as np
import pytest
from typer.testing import CliRunner

from talk_autopsy import align as align_module
from talk_autopsy import cli
from talk_autopsy.align import align, label_ids, number_words, spoken_form, viterbi
from talk_autopsy.segments import Segment, Transcript, load_transcript, save_transcript

runner = CliRunner()

LABELS = ("-", "|", *"ABCDEFGHIJKLMNOPQRSTUVWXYZ'")
HIGH, LOW = math.log(0.9), math.log(0.004)


class StubEmitter:
    """Every frame is blank, except the frames in `script` (frame -> label), which are that label."""

    name = "stub"
    version = "0"
    labels = LABELS
    blank = 0
    word_separator = 1
    frame_seconds = 0.02

    def __init__(self, script: dict[int, str]):
        self.script = script

    def emissions(self, samples, sample_rate):
        frames = math.ceil(len(samples) / 320)
        out = np.full((frames, len(LABELS)), LOW, dtype=np.float32)
        out[:, 0] = HIGH
        for frame, label in self.script.items():
            out[frame, 0] = LOW
            out[frame, LABELS.index(label)] = HIGH
        return out


def spoken(words_at: list[tuple[int, str]]) -> dict[int, str]:
    """Script for words spoken from the given frames: one letter per frame, then a separator frame."""
    script = {}
    for frame, word in words_at:
        for offset, ch in enumerate(word.upper()):
            script[frame + offset] = ch
        script[frame + len(word)] = "|"
    return script


@pytest.fixture
def silence(tmp_path):
    def make(seconds: float):
        path = tmp_path / "a.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(np.zeros(round(seconds * 16000), dtype="<i2").tobytes())
        return path
    return make


def transcript(*segments: tuple[float, float, str]) -> Transcript:
    return Transcript(segments=[Segment(id=10 + n, start=s, end=e, text=t)
                                for n, (s, e, t) in enumerate(segments)],
                      language="en", source="gemini test", meta={"timestamps": "model-estimated"})


# --- text -------------------------------------------------------------------------


def test_number_words():
    assert number_words(0) == "zero"
    assert number_words(42) == "forty two"
    assert number_words(2025) == "two thousand twenty five"
    assert number_words(100) == "one hundred"


def test_spoken_form_spells_digits_and_symbols():
    assert spoken_form("Q&A") == "Q and A"
    assert spoken_form("Claude 4") == "Claude four"
    assert spoken_form("50%") == "fifty percent"
    assert spoken_form("/terminal-setup,") == "terminalsetup"
    assert spoken_form("-") == ""


def test_label_ids_put_the_separator_between_spoken_parts():
    ids = label_ids("Q&A", StubEmitter({}))
    assert [LABELS[i] for i in ids] == list("Q|AND|A")


# --- Viterbi ----------------------------------------------------------------------


def test_viterbi_finds_the_tokens_between_garbage():
    emitter = StubEmitter(spoken([(30, "ab")]))
    emissions = emitter.emissions(np.zeros(100 * 320), 16000)
    tokens = [LABELS.index("A"), LABELS.index("B")]
    placements, _ = viterbi(emissions, tokens, blank=0)
    assert [p.emit for p in placements] == [30, 31]


def test_viterbi_returns_none_when_the_tokens_do_not_fit():
    assert viterbi(np.zeros((1, len(LABELS))), [2, 3], blank=0) is None


# --- the stage --------------------------------------------------------------------


def test_words_are_retimed_and_segments_follow_their_words(silence):
    audio = silence(6.0)
    # spoken: "hello world" at 1.0 s, "good bye" at 3.0 s; the input's estimates are off by seconds
    emitter = StubEmitter(spoken([(50, "hello"), (60, "world"), (150, "good"), (158, "bye")]))
    source = transcript((0.0, 0.5, "Hello world."), (4.5, 5.9, "Good bye."))
    result = align(source, audio, emitter)
    assert result.ids() == [10, 11]
    assert [s.text for s in result.segments] == ["Hello world.", "Good bye."]
    first, second = result.segments
    assert [w.text for w in first.words] == ["Hello", "world."]
    assert first.words[0].start == pytest.approx(1.0) and first.words[1].start == pytest.approx(1.2)
    assert first.words[0].end == pytest.approx(1.1)  # last letter at frame 54
    assert first.start == first.words[0].start and first.end == first.words[-1].end
    assert second.start == pytest.approx(3.0)
    assert all(w.probability and w.probability > 0.5 for s in result.segments for w in s.words)
    meta = result.meta
    assert meta["timestamps"] == "aligned" and meta["timestamps_before_alignment"] == "model-estimated"
    assert meta["alignment"]["aligner"] == "stub" and meta["alignment"]["unplaced_words"] == 0
    assert result.source == "gemini test + aligned"


def test_a_word_without_letters_is_interpolated_and_flagged(silence):
    audio = silence(4.0)
    emitter = StubEmitter(spoken([(50, "one"), (100, "two")]))
    result = align(transcript((0.0, 3.0, "one - two")), audio, emitter)
    words = result.segments[0].words
    dash = words[1]
    assert dash.probability is None
    assert words[0].end <= dash.start <= dash.end <= words[2].start
    assert result.meta["alignment"]["unplaced"] == ["10:1"]
    assert result.meta["alignment"]["unplaced_share"] == pytest.approx(1 / 3, abs=1e-4)


def test_blocks_chain_through_long_audio(silence, monkeypatch):
    monkeypatch.setattr(align_module, "BLOCK_CHARS", 5)  # one segment per block
    audio = silence(40.0)
    words = [(100 + 300 * n, w) for n, w in enumerate(["alpha", "bravo", "charlie", "delta", "echo"])]
    emitter = StubEmitter(spoken(words))
    # all estimates squeezed into the first two seconds: only the lengths are used
    source = transcript(*[(0.4 * n, 0.4 * n + 0.4, w) for n, (_, w) in enumerate(words)])
    result = align(source, audio, emitter)
    assert [s.start for s in result.segments] == pytest.approx([2.0, 8.0, 14.0, 20.0, 26.0])
    assert result.meta["alignment"]["blocks"] == 5 and result.meta["alignment"]["blocks_failed"] == []


def test_missing_audio_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        align(transcript((0, 1, "a")), tmp_path / "none.wav", StubEmitter({}))


def test_align_command_writes_the_json(silence, tmp_path, monkeypatch):
    audio = silence(3.0)
    source_path = save_transcript(transcript((0.0, 1.0, "Hello")), tmp_path / "raw.json")
    monkeypatch.setattr(align_module, "Wav2Vec2Emitter", lambda: StubEmitter(spoken([(40, "hello")])))
    out = tmp_path / "aligned.json"
    result = runner.invoke(cli.app, ["align", str(source_path), str(audio), "--out", str(out)])
    assert result.exit_code == 0, result.output
    aligned = load_transcript(out)
    assert aligned.segments[0].start == pytest.approx(0.8)
    assert "0 unplaced" in result.output


def test_start_differences_on_agreed_words():
    def timed(words):
        segment_words = [align_module.Word(start=s, end=s + 0.2, text=w) for w, s in words]
        text = " ".join(w for w, _ in words)
        return Transcript(segments=[Segment(id=1, start=words[0][1], end=words[-1][1] + 0.2, text=text,
                                            words=segment_words)], language="en", source="t")

    a = timed([("open", 1.0), ("Claude", 1.5), ("Code", 2.0), ("now", 2.5)])
    b = timed([("open", 1.1), ("cloud", 1.4), ("Code", 2.3), ("now", 2.5)])
    assert align_module.start_differences(a, b) == pytest.approx([0.1, 0.3, 0.0])
