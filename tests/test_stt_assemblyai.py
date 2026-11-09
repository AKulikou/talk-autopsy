"""
The AssemblyAI engine against a fake `assemblyai` package: scripted responses,
no upload, no network.
"""

import sys
import wave
from enum import Enum
from types import ModuleType, SimpleNamespace

import pytest
from typer.testing import CliRunner

from talk_autopsy import cli
from talk_autopsy.config import ConfigurationError
from talk_autopsy.glossary import Glossary, GlossaryEntry
from talk_autopsy.segments import Segment, Word, load_transcript
from talk_autopsy.stt.assemblyai import AssemblyAITranscriber, keyterms, segments_from_response, split_words
from talk_autopsy.stt.base import Transcriber, drop_wordless

runner = CliRunner()


def w(start_ms, end_ms, text, confidence=0.9):
    return SimpleNamespace(start=start_ms, end=end_ms, text=text, confidence=confidence)


WORDS = [w(0, 400, "Open", 0.9), w(450, 900, "Claude", 0.5), w(950, 1500, "Code.", 0.7),
         w(3000, 3400, "Then", 1.0), w(3450, 4000, "npm.", 0.8)]
SENTENCES = [SimpleNamespace(text="Open Claude Code.", start=0, end=1500, words=WORDS[:3]),
             SimpleNamespace(text="Then npm.", start=3000, end=4000, words=WORDS[3:])]


class Status(Enum):
    completed = "completed"
    error = "error"


@pytest.fixture
def fake_aai(monkeypatch):
    """Install a fake assemblyai package; returns a record of the calls and a knob for the response."""
    calls = SimpleNamespace(transcribe=[], response={})

    class TranscriptionConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Transcriber:
        def transcribe(self, data, config=None):
            calls.transcribe.append((data, config.kwargs, module.settings.api_key))
            r = {"status": Status.completed, "error": None, "sentences": SENTENCES, "words": WORDS,
                 "audio_duration": 600.0, "speech_model_used": config.kwargs["speech_models"][0]}
            r.update(calls.response)
            return SimpleNamespace(id="tr_123", status=r["status"], error=r["error"], words=r["words"],
                                   audio_duration=r["audio_duration"],
                                   speech_model_used=r["speech_model_used"],
                                   get_sentences=lambda: r["sentences"])

    module = ModuleType("assemblyai")
    module.settings = SimpleNamespace(api_key=None)
    module.TranscriptionConfig = TranscriptionConfig
    module.Transcriber = Transcriber
    module.TranscriptStatus = Status
    module.__version__ = "fake"
    monkeypatch.setitem(sys.modules, "assemblyai", module)
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "aai-test-key")
    return calls


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00\x00" * 1600)
    return path


GLOSSARY = Glossary(entries=[GlossaryEntry(term="Claude Code", keep=True),
                             GlossaryEntry(term="npm", keep=True),
                             GlossaryEntry(term="one two three four five six seven", keep=False)])


def test_engine_implements_the_transcriber_protocol():
    assert isinstance(AssemblyAITranscriber(), Transcriber)


def test_settings_reach_the_service(fake_aai, audio):
    AssemblyAITranscriber().transcribe(audio, None)
    data, settings, key = fake_aai.transcribe[0]
    assert data == str(audio) and key == "aai-test-key"
    assert settings == {"speech_models": ["universal-3-5-pro"], "language_code": "en",
                        "speaker_labels": False}


def test_sentences_become_segments_with_seconds_and_mean_confidence(fake_aai, audio):
    t = AssemblyAITranscriber().transcribe(audio, None)
    assert [s.text for s in t.segments] == ["Open Claude Code.", "Then npm."]
    first = t.segments[0]
    assert (first.id, first.start, first.end) == (1, 0.0, 1.5)
    assert first.words[1] == Word(start=0.45, end=0.9, text="Claude", probability=0.5)
    assert first.confidence == pytest.approx((0.9 + 0.5 + 0.7) / 3)


def test_meta_records_the_run(fake_aai, audio):
    meta = AssemblyAITranscriber().transcribe(audio, None).meta
    assert meta["engine"] == "assemblyai" and meta["model"] == "universal-3-5-pro"
    assert meta["request_id"] == "tr_123" and meta["glossary_prompt"] is False and meta["keyterms"] is None
    assert meta["audio_seconds"] == 600.0 and meta["billed_seconds"] == 600.0
    assert meta["cost_usd"] == pytest.approx(600 / 3600 * 0.21, abs=1e-4)
    assert meta["timestamps"] == "measured" and meta["segmented_by"] == "sentences"


def test_glossary_prompt_sends_keyterms_within_the_word_limit(fake_aai, audio):
    t = AssemblyAITranscriber(glossary_prompt=True).transcribe(audio, GLOSSARY)
    assert fake_aai.transcribe[0][1]["keyterms_prompt"] == ["Claude Code", "npm"]
    assert t.meta["keyterms"] == ["Claude Code", "npm"] and t.meta["glossary_prompt"] is True


def test_glossary_prompt_needs_a_glossary(fake_aai, audio):
    with pytest.raises(ValueError, match="no glossary"):
        AssemblyAITranscriber(glossary_prompt=True).transcribe(audio, None)


def test_without_sentences_words_are_split_at_pauses(fake_aai, audio):
    fake_aai.response = {"sentences": []}
    t = AssemblyAITranscriber().transcribe(audio, None)
    assert [s.text for s in t.segments] == ["Open Claude Code.", "Then npm."]
    assert t.meta["segmented_by"] == "pauses"


def test_service_error_is_raised_with_the_request_id(fake_aai, audio):
    fake_aai.response = {"status": Status.error, "error": "audio too short"}
    with pytest.raises(RuntimeError, match="tr_123 failed: audio too short"):
        AssemblyAITranscriber().transcribe(audio, None)


def test_other_model_than_requested_is_recorded(fake_aai, audio):
    fake_aai.response = {"speech_model_used": "universal-2"}
    t = AssemblyAITranscriber().transcribe(audio, None)
    assert t.meta["model"] == "universal-2" and t.meta["model_requested"] == "universal-3-5-pro"


def test_missing_key_fails_before_any_upload(fake_aai, audio, monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "")
    with pytest.raises(ConfigurationError, match="ASSEMBLYAI_API_KEY"):
        AssemblyAITranscriber().transcribe(audio, None)
    assert fake_aai.transcribe == []


# --- pure helpers ----------------------------------------------------------


def _words(*spans):
    return [Word(start=a, end=b, text=f"w{i}") for i, (a, b) in enumerate(spans)]


def test_split_at_a_pause_longer_than_the_limit():
    groups = split_words(_words((0, 1), (1.7, 2), (2.71, 3)), pause=0.7)
    assert [len(g) for g in groups] == [2, 1]


def test_split_after_the_word_limit():
    groups = split_words(_words(*[(i, i + 0.5) for i in range(7)]), max_words=3)
    assert [len(g) for g in groups] == [3, 3, 1]


def test_sentences_without_words_are_skipped():
    sentences = [SimpleNamespace(text="", words=[]), *SENTENCES]
    segments, how = segments_from_response(sentences, WORDS)
    assert [s.id for s in segments] == [1, 2] and how == "sentences"


def test_keyterms_leave_out_terms_over_six_words():
    assert keyterms(GLOSSARY) == ["Claude Code", "npm"]


def test_drop_wordless_renumbers():
    segments = [Segment(id=1, start=0, end=1, text="Hello."), Segment(id=2, start=1, end=2, text=" . "),
                Segment(id=3, start=2, end=3, text="Bye.")]
    kept, dropped = drop_wordless(segments)
    assert [(s.id, s.text) for s in kept] == [(1, "Hello."), (2, "Bye.")] and dropped == 1


# --- CLI -------------------------------------------------------------------


def test_transcribe_command_with_the_assemblyai_engine(fake_aai, audio, tmp_path):
    out = tmp_path / "raw.assemblyai.json"
    result = runner.invoke(cli.app, ["transcribe", str(audio), "--engine", "assemblyai", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert load_transcript(out).meta["engine"] == "assemblyai"
    assert "2 segments" in result.output and "0.0350 USD" in result.output


def test_unknown_engine_is_a_usage_error(audio, tmp_path):
    args = ["transcribe", str(audio), "--engine", "nope", "--out", str(tmp_path / "o")]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 2


def test_missing_key_is_reported_without_a_traceback(fake_aai, audio, tmp_path, monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "")
    result = runner.invoke(cli.app, ["transcribe", str(audio), "--engine", "assemblyai",
                                     "--out", str(tmp_path / "o.json")])
    assert result.exit_code == 1 and "ASSEMBLYAI_API_KEY is not set" in result.output
