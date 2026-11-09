"""
The Gemini engine against a fake `google.genai` package: scripted answers, no
upload, no network.
"""

import json
import sys
import wave
from enum import Enum
from types import ModuleType, SimpleNamespace

import pytest
from typer.testing import CliRunner

from talk_autopsy import cli
from talk_autopsy.glossary import Glossary, GlossaryEntry
from talk_autopsy.segments import load_transcript
from talk_autopsy.stt import gemini
from talk_autopsy.stt.base import Transcriber
from talk_autopsy.stt.gemini import GeminiTranscriber, parse_answer

runner = CliRunner()

ANSWER = [{"start_seconds": 0.0, "end_seconds": 2.5, "text": "Open Claude Code."},
          {"start_seconds": 2.5, "end_seconds": 2.6, "text": "."},
          {"start_seconds": 3.0, "end_seconds": 4.0, "text": "Then run /theme."}]


class FileState(Enum):
    PROCESSING = "PROCESSING"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"


class FinishReason(Enum):
    STOP = "STOP"
    MAX_TOKENS = "MAX_TOKENS"


class APIError(Exception):
    def __init__(self, code, status):
        super().__init__(f"{code} {status}")
        self.code, self.status = code, status


class ClientError(APIError):
    pass


class ServerError(APIError):
    pass


def response(text, finish=FinishReason.STOP, model="gemini-3.8-flash"):
    return SimpleNamespace(
        text=text, candidates=[SimpleNamespace(finish_reason=finish)], prompt_feedback=None,
        model_version=model, response_id="resp_1",
        usage_metadata=SimpleNamespace(prompt_token_count=19200, candidates_token_count=3000,
                                       thoughts_token_count=1000, total_token_count=23200))


@pytest.fixture
def fake_genai(monkeypatch):
    """
    Install fake google.genai, .types and .errors. `state.answers` maps a model to
    a response or an exception; every call is recorded.
    """
    state = SimpleNamespace(answers={"gemini-3.8-flash": response(json.dumps(ANSWER))}, generate=[],
                            uploads=[], deleted=[], gets=0, keys=[], file_states=[])

    def upload(file, config):
        state.uploads.append((file, config.kwargs))
        return SimpleNamespace(name="files/abc", state=FileState.PROCESSING, error=None)

    def get(name):
        state.gets += 1
        file_state = state.file_states.pop(0) if state.file_states else FileState.ACTIVE
        return SimpleNamespace(name=name, state=file_state, error="bad audio")

    def generate_content(model, contents, config):
        state.generate.append((model, contents, config.kwargs))
        answer = state.answers[model]
        if isinstance(answer, list):  # one answer per call
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    class Client:
        def __init__(self, api_key):
            state.keys.append(api_key)
            self.files = SimpleNamespace(upload=upload, get=get,
                                         delete=lambda name: state.deleted.append(name))
            self.models = SimpleNamespace(generate_content=generate_content)

    class Config:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    genai = ModuleType("google.genai")
    genai.Client = Client
    genai.__version__ = "fake"
    types = ModuleType("google.genai.types")
    types.UploadFileConfig = Config
    types.GenerateContentConfig = Config
    types.FileState = FileState
    types.FinishReason = FinishReason
    errors = ModuleType("google.genai.errors")
    errors.APIError = APIError
    errors.ClientError = ClientError
    errors.ServerError = ServerError
    for name, module in (("google.genai", genai), ("google.genai.types", types),
                         ("google.genai.errors", errors)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test-key")
    monkeypatch.setattr(gemini, "POLL_SECONDS", 0.0)
    monkeypatch.setattr(gemini, "RETRY_WAIT_SECONDS", (0, 0))
    return state


@pytest.fixture
def audio(tmp_path):
    """10 s of silence, 16 kHz mono."""
    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00\x00" * 160000)
    return path


def engine(**kwargs):
    return GeminiTranscriber(model="gemini-3.8-flash", fallback_model="gemini-3.6-flash", tier="free",
                             **kwargs)


def test_engine_implements_the_transcriber_protocol():
    assert isinstance(engine(), Transcriber)


def test_upload_waits_for_active_then_asks_for_structured_json(fake_genai, audio):
    engine().transcribe(audio, None)
    assert fake_genai.keys == ["gem-test-key"]
    assert fake_genai.uploads[0][1]["mime_type"] == "audio/wav" and fake_genai.gets == 1
    model, contents, config = fake_genai.generate[0]
    assert model == "gemini-3.8-flash" and contents[0].name == "files/abc"
    assert config["response_mime_type"] == "application/json"
    assert config["response_json_schema"]["type"] == "array"
    assert fake_genai.deleted == ["files/abc"]


def test_segments_have_no_confidence_and_wordless_ones_are_dropped(fake_genai, audio):
    t = engine().transcribe(audio, None)
    assert [(s.id, s.text) for s in t.segments] == [(1, "Open Claude Code."), (2, "Then run /theme.")]
    assert all(s.confidence is None and s.words is None for s in t.segments)
    assert t.meta["segments_dropped_wordless"] == 1


def test_meta_marks_timestamps_as_estimates_and_records_tokens(fake_genai, audio):
    meta = engine().transcribe(audio, None).meta
    assert meta["timestamps"] == "model-estimated" and meta["model"] == "gemini-3.8-flash"
    assert meta["fallback_reason"] is None and meta["request_id"] == "resp_1"
    assert meta["tokens"] == {"prompt": 19200, "output": 3000, "thinking": 1000, "total": 23200}
    assert meta["cost_usd"] == 0.0
    assert meta["cost_usd_at_paid_prices"] == pytest.approx((19200 * 0.75 + 4000 * 3.75) / 1e6)
    assert meta["audio_seconds"] == 10.0


def test_paid_tier_records_the_estimate_as_cost(fake_genai, audio):
    meta = GeminiTranscriber(model="gemini-3.8-flash", tier="paid").transcribe(audio, None).meta
    assert meta["cost_usd"] == meta["cost_usd_at_paid_prices"] > 0


def test_quota_rejection_falls_back_once_and_is_recorded(fake_genai, audio):
    fake_genai.answers = {"gemini-3.8-flash": ClientError(429, "RESOURCE_EXHAUSTED"),
                          "gemini-3.6-flash": response(json.dumps(ANSWER), model="gemini-3.6-flash")}
    t = engine().transcribe(audio, None)
    assert [g[0] for g in fake_genai.generate] == ["gemini-3.8-flash", "gemini-3.6-flash"]
    assert t.meta["model"] == "gemini-3.6-flash" and t.meta["model_requested"] == "gemini-3.8-flash"
    assert "429" in t.meta["fallback_reason"]


def test_other_client_errors_do_not_fall_back(fake_genai, audio):
    fake_genai.answers = {"gemini-3.8-flash": ClientError(400, "INVALID_ARGUMENT")}
    with pytest.raises(RuntimeError, match="gemini-3.8-flash failed: 400 INVALID_ARGUMENT"):
        engine().transcribe(audio, None)
    assert len(fake_genai.generate) == 1 and fake_genai.deleted == ["files/abc"]


def test_cut_off_answer_is_an_error(fake_genai, audio):
    fake_genai.answers = {"gemini-3.8-flash": response("[", finish=FinishReason.MAX_TOKENS)}
    with pytest.raises(RuntimeError, match="MAX_TOKENS"):
        engine().transcribe(audio, None)


def test_failed_upload_is_an_error(fake_genai, audio):
    fake_genai.file_states = [FileState.FAILED]
    with pytest.raises(RuntimeError, match="could not process"):
        engine().transcribe(audio, None)


def test_glossary_goes_into_the_prompt_only_when_asked(fake_genai, audio):
    glossary = Glossary(entries=[GlossaryEntry(term="/terminal-setup", keep=True)])
    engine().transcribe(audio, glossary)
    assert "/terminal-setup" not in fake_genai.generate[0][1][1]
    t = engine(glossary_prompt=True).transcribe(audio, glossary)
    assert "- /terminal-setup" in fake_genai.generate[1][1][1] and t.meta["glossary_prompt"] is True


@pytest.mark.parametrize("text, message", [
    ("", "empty"),
    ('[{"start_seconds": 0, "end_seconds": 1, "text": "a"},]', "malformed JSON"),
    ('{"segments": []}', "shape"),
    ('[{"start_seconds": 0, "text": "a"}]', "shape"),
    ('[{"start_seconds": 5, "end_seconds": 1, "text": "a"}]', "before start"),
])
def test_malformed_answers_are_rejected_not_repaired(text, message):
    with pytest.raises(ValueError, match=message):
        parse_answer(text)


def test_unknown_tier_is_rejected():
    with pytest.raises(ValueError, match="GEMINI_TIER"):
        GeminiTranscriber(tier="enterprise")


def test_transcribe_command_with_the_gemini_engine(fake_genai, audio, tmp_path):
    out = tmp_path / "raw.gemini.json"
    result = runner.invoke(cli.app, ["transcribe", str(audio), "--engine", "gemini", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert load_transcript(out).meta["timestamps"] == "model-estimated"
    assert "2 segments" in result.output


def test_server_error_is_retried_on_the_same_model_and_recorded(fake_genai, audio):
    fake_genai.answers = {"gemini-3.8-flash": [ServerError(503, "UNAVAILABLE"), response(json.dumps(ANSWER))]}
    t = engine().transcribe(audio, None)
    assert [g[0] for g in fake_genai.generate] == ["gemini-3.8-flash", "gemini-3.8-flash"]
    assert t.meta["model"] == "gemini-3.8-flash"
    failed = {"model": "gemini-3.8-flash", "attempt": 1, "error": "503 UNAVAILABLE"}
    assert t.meta["failed_attempts"] == [failed]


def test_server_errors_past_the_retries_fail_cleanly(fake_genai, audio):
    fake_genai.answers = {"gemini-3.8-flash": [ServerError(503, "UNAVAILABLE") for _ in range(3)]}
    with pytest.raises(RuntimeError, match="503 UNAVAILABLE"):
        engine().transcribe(audio, None)
    assert len(fake_genai.generate) == 3 and fake_genai.deleted == ["files/abc"]
