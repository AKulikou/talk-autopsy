"""
The faster-whisper engine against a fake `faster_whisper` package: scripted
segments, no model download, no audio decoding.
"""

import math
import re
import sys
import wave
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest
from typer.testing import CliRunner

from talk_autopsy import cli
from talk_autopsy.glossary import Glossary, GlossaryEntry
from talk_autopsy.segments import load_transcript
from talk_autopsy.stt import faster_whisper as fw
from talk_autopsy.stt.base import Transcriber
from talk_autopsy.stt.faster_whisper import (
    FasterWhisperTranscriber,
    build_glossary_prompt,
    estimate_tokens,
)

runner = CliRunner()

ANSI = re.compile(chr(27) + r"\[[0-9;]*m")  # ESC [ ... m


def plain(output: str) -> str:
    """CLI output without ANSI colours: on GitHub Actions Typer colours the usage errors."""
    return ANSI.sub("", output)


def _word(start, end, text, probability):
    return SimpleNamespace(start=start, end=end, word=text, probability=probability)


SCRIPT = [
    SimpleNamespace(start=0.0, end=2.5, text=" Open cloud code.", avg_logprob=-0.2,
                    words=[_word(0.0, 0.6, " Open", 0.98), _word(0.7, 1.4, " cloud", 0.41),
                           _word(1.5, 2.5, " code.", 0.9)]),
    SimpleNamespace(start=61.0, end=63.0, text=" Then npm install.", avg_logprob=-1.0,
                    words=[_word(61.0, 61.5, " Then", 0.95), _word(61.6, 62.2, " npm", 0.7),
                           _word(62.3, 63.0, " install.", 0.92)]),
]


class FakeTokenizer:
    """One token per word: enough to check that the engine counts with the model's tokenizer."""

    def encode(self, text, add_special_tokens=True):
        return SimpleNamespace(ids=list(range(len(text.split()))))


@pytest.fixture
def fake_whisper(monkeypatch):
    """Install a fake faster_whisper package; returns the list of created models."""
    models = []

    class WhisperModel:
        def __init__(self, model_size, device, compute_type):
            self.args = (model_size, device, compute_type)
            self.calls = []
            self.hf_tokenizer = FakeTokenizer()
            models.append(self)

        def transcribe(self, audio, **kwargs):
            self.calls.append((audio, kwargs))
            info = SimpleNamespace(duration=64.0, duration_after_vad=5.5, language="en")
            return iter(SCRIPT), info

    module = ModuleType("faster_whisper")
    module.WhisperModel = WhisperModel
    module.__version__ = "fake"
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    monkeypatch.setattr(fw, "announce_download", lambda model_size: None)
    return models


@pytest.fixture
def audio(tmp_path):
    """0.5 s of 16 kHz mono 16-bit PCM: a ramp of known samples."""
    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(np.arange(-4000, 4000, dtype="<i2").tobytes())
    return path


GLOSSARY = Glossary(entries=[GlossaryEntry(term="Claude Code", keep=True),
                             GlossaryEntry(term="npm", keep=True)])


def test_engine_implements_the_transcriber_protocol():
    engine = FasterWhisperTranscriber()
    assert isinstance(engine, Transcriber)
    assert engine.name == "faster-whisper large-v3"


def test_settings_reach_the_model(fake_whisper, audio):
    FasterWhisperTranscriber().transcribe(audio, None)
    model = fake_whisper[0]
    assert model.args == ("large-v3", "cpu", "int8")
    samples, kwargs = model.calls[0]
    assert kwargs == {"language": "en", "beam_size": 5, "vad_filter": True,
                      "word_timestamps": True, "initial_prompt": None}
    # the WAV goes in as float32 samples, not as a path for PyAV to decode
    assert samples.dtype == np.float32 and samples.shape == (8000,)
    assert samples[0] == pytest.approx(-4000 / 32768) and samples[-1] == pytest.approx(3999 / 32768)


def test_audio_in_another_format_is_refused_before_the_model_loads(fake_whisper, sine):
    with pytest.raises(ValueError, match="talk-autopsy cut"):
        FasterWhisperTranscriber().transcribe(sine, None)  # 44.1 kHz stereo
    assert fake_whisper == []


def test_a_file_that_is_not_a_wav_is_refused(fake_whisper, tmp_path):
    path = tmp_path / "x.wav"
    path.write_bytes(b"not audio")
    with pytest.raises(ValueError, match="not a PCM WAV"):
        FasterWhisperTranscriber().transcribe(path, None)


def test_segments_words_and_confidence(fake_whisper, audio):
    transcript = FasterWhisperTranscriber().transcribe(audio, GLOSSARY)
    assert transcript.ids() == [1, 2]
    first = transcript.segments[0]
    assert first.text == "Open cloud code."
    assert first.confidence == pytest.approx(math.exp(-0.2))
    assert [w.text for w in first.words] == ["Open", "cloud", "code."]
    assert first.words[1].probability == 0.41
    assert transcript.source == "faster-whisper large-v3" and transcript.language == "en"


def test_meta_records_the_run(fake_whisper, audio):
    meta = FasterWhisperTranscriber().transcribe(audio, GLOSSARY).meta
    assert meta["engine"] == "faster-whisper" and meta["model"] == "large-v3"
    assert meta["compute_type"] == "int8"
    assert meta["glossary_prompt"] is False and meta["initial_prompt"] is None
    assert meta["audio_seconds"] == 64.0 and meta["cost_usd"] == 0.0
    assert meta["wall_seconds"] >= meta["transcribe_seconds"] >= 0
    assert meta["realtime_factor"] is None or meta["realtime_factor"] > 0


def test_glossary_prompt_is_passed_and_recorded(fake_whisper, audio):
    transcript = FasterWhisperTranscriber(glossary_prompt=True).transcribe(audio, GLOSSARY)
    _, kwargs = fake_whisper[0].calls[0]
    assert kwargs["initial_prompt"] == "Claude Code, npm"
    assert transcript.meta["glossary_prompt"] is True
    assert transcript.meta["initial_prompt"] == "Claude Code, npm"


def test_glossary_prompt_needs_a_glossary(fake_whisper, audio):
    with pytest.raises(ValueError, match="no glossary"):
        FasterWhisperTranscriber(glossary_prompt=True).transcribe(audio, None)


def test_missing_audio_is_an_error(fake_whisper, tmp_path):
    with pytest.raises(FileNotFoundError):
        FasterWhisperTranscriber().transcribe(tmp_path / "none.wav", None)


# --- prompt builder --------------------------------------------------------


def _long_glossary(n):
    return Glossary(entries=[GlossaryEntry(term=f"identifier-number-{i:03d}", keep=True) for i in range(n)])


def test_prompt_is_capped_at_whole_terms():
    glossary = _long_glossary(300)
    prompt = build_glossary_prompt(glossary)
    assert estimate_tokens(prompt) <= 200
    terms = prompt.split(", ")
    assert terms == [e.term for e in glossary.entries[:len(terms)]]  # glossary order, no term cut
    assert 0 < len(terms) < 300
    longer = ", ".join([*terms, glossary.entries[len(terms)].term])
    assert estimate_tokens(longer) > 200  # the next term would not have fitted


def test_prompt_uses_the_given_token_counter():
    words = lambda text: len(text.split())  # noqa: E731 - "a, b" is two tokens
    prompt = build_glossary_prompt(_long_glossary(50), max_tokens=10, count_tokens=words)
    assert len(prompt.split(", ")) == 10


def test_prompt_cap_cannot_exceed_whisper_limit():
    with pytest.raises(ValueError, match="224"):
        build_glossary_prompt(_long_glossary(3), max_tokens=225)


def test_empty_glossary_gives_no_prompt():
    assert build_glossary_prompt(Glossary()) is None


# --- CLI -------------------------------------------------------------------


def test_transcribe_command_writes_the_json(fake_whisper, audio, tmp_path):
    glossary = tmp_path / "T9.glossary.md"
    glossary.write_text("| term | keep |\n|---|---|\n| npm | yes |\n", encoding="utf-8")
    out = tmp_path / "work" / "raw.json"
    args = ["transcribe", str(audio), "--out", str(out), "--glossary", str(glossary), "--glossary-prompt"]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    transcript = load_transcript(out)
    assert transcript.meta["initial_prompt"] == "npm" and transcript.meta["glossary"] == "T9.glossary.md"
    assert "2 segments" in result.output


def test_glossary_prompt_flag_needs_a_glossary_file(audio, tmp_path):
    result = runner.invoke(cli.app, ["transcribe", str(audio), "--out", str(tmp_path / "o.json"),
                                     "--glossary-prompt"])
    assert result.exit_code == 2 and "--glossary" in plain(result.output)


def test_wordless_segments_are_dropped_and_ids_follow(fake_whisper, audio, monkeypatch):
    dot = SimpleNamespace(start=63.0, end=64.0, text=" .", avg_logprob=-0.5, words=[])
    monkeypatch.setattr(sys.modules[__name__], "SCRIPT", [SCRIPT[0], dot, SCRIPT[1]])
    transcript = FasterWhisperTranscriber().transcribe(audio, None)
    texts = [(s.id, s.text) for s in transcript.segments]
    assert texts == [(1, "Open cloud code."), (2, "Then npm install.")]
    assert transcript.meta["segments_dropped_wordless"] == 1
