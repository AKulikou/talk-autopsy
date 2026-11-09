"""Chunked Gemini transcription with fake per-chunk engines: no network, no model."""

import shutil
import subprocess
import wave
from types import SimpleNamespace

import pytest

from talk_autopsy.segments import Segment, Transcript
from talk_autopsy.stt import chunked, gemini
from talk_autopsy.stt.chunked import ChunkedGeminiTranscriber, parse_silences, plan_chunks
from talk_autopsy.stt.gemini import GeminiQuotaExhausted, GeminiUnavailable, is_daily_quota_or_billing

REAL_DETECT_SILENCES = chunked.detect_silences  # the engine tests replace it

# --- plan ---------------------------------------------------------------------------


def test_short_audio_is_one_chunk():
    assert plan_chunks(600.0, []) == [(0.0, 600.0)]


def test_cuts_go_to_the_longest_silence_near_each_boundary():
    silences = [(590.0, 591.0), (605.0, 608.0), (640.0, 650.0),  # 640-650 is outside +-30 s of 600
                (1195.0, 1196.0)]
    assert plan_chunks(1500.0, silences) == [(0.0, 606.5), (606.5, 1195.5), (1195.5, 1500.0)]


def test_without_a_silence_the_cut_is_at_the_boundary():
    assert plan_chunks(1300.0, []) == [(0.0, 600.0), (600.0, 1200.0), (1200.0, 1300.0)]


def test_parse_silences_closes_an_open_silence_at_the_end():
    text = ("[silencedetect @ 0x1] silence_start: 1.5\n[silencedetect @ 0x1] silence_end: 2.25 | "
            "silence_duration: 0.75\n[silencedetect @ 0x1] silence_start: 9.8\n")
    assert parse_silences(text, 10.0) == [(1.5, 2.25), (9.8, 10.0)]


def test_detect_silences_with_ffmpeg(tmp_path):
    path = tmp_path / "tone-gap-tone.wav"
    graph = ("sine=f=440:d=1:r=16000[a];anullsrc=r=16000:cl=mono,atrim=0:1[b];"
             "sine=f=440:d=1:r=16000[c];[a][b][c]concat=n=3:v=0:a=1")
    subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-filter_complex", graph, "-ac", "1", "-ar", "16000", str(path)], check=True)
    silences = REAL_DETECT_SILENCES(path, 3.0)
    assert len(silences) == 1
    start, end = silences[0]
    assert start == pytest.approx(1.0, abs=0.05) and end == pytest.approx(2.0, abs=0.05)


# --- errors ---------------------------------------------------------------------------


@pytest.mark.parametrize("code, text, expected", [
    (429, "RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier", True),
    (429, "RESOURCE_EXHAUSTED You exceeded your current quota, check your plan and billing details. "
          "quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier", False),
    (400, "FAILED_PRECONDITION billing is not enabled for this project", True),
    (503, "UNAVAILABLE high demand", False),
])
def test_daily_quota_and_billing_are_told_from_rate_limits(code, text, expected):
    assert is_daily_quota_or_billing(SimpleNamespace(code=code, message=text)) is expected


# --- the engine ---------------------------------------------------------------------


@pytest.fixture
def long_audio(tmp_path):
    """12 s of silence; with chunk_seconds=5 it becomes three chunks."""
    path = tmp_path / "talk.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * 12)
    return path


class FakeEngine:
    """Answers each chunk with two segments at 0.5-1.5 s and 2-9 s (the second runs past a 5 s chunk)."""

    def __init__(self, name, log, fail=None):
        self.name, self.log, self.fail = name, log, fail or {}

    def transcribe(self, wav, glossary):
        self.log.append((self.name, wav.name))
        error = self.fail.get(len([1 for n, _ in self.log if n == self.name]))
        if error is not None:
            raise error
        segments = [Segment(id=1, start=0.5, end=1.5, text=f"{self.name} one"),
                    Segment(id=2, start=2.0, end=9.0, text=f"{self.name} two")]
        return Transcript(segments=segments, language="en", source=self.name,
                          meta={"model": self.name, "tokens": {"prompt": 10, "output": 5, "thinking": 0},
                                "cost_usd": 0.0, "cost_usd_at_paid_prices": 0.01, "wall_seconds": 1.0})


def engine(tmp_path, log, fail=None, whisper_fail=None, events=None, models=("m1", "m2")):
    fail = fail or {}
    return ChunkedGeminiTranscriber(
        models=list(models), cache_dir=tmp_path / "chunks",
        progress=(events.append if events is not None else None),
        gemini_factory=lambda model: FakeEngine(model, log, fail.get(model)),
        whisper_factory=lambda: FakeEngine("whisper", log, whisper_fail),
        chunk_seconds=5.0, search_seconds=1.0)


@pytest.fixture(autouse=True)
def silences(monkeypatch):
    monkeypatch.setattr(chunked, "detect_silences", lambda audio, duration: [(5.4, 5.8)])


def test_chunks_are_cut_shifted_and_numbered_once(tmp_path, long_audio):
    log = []
    t = engine(tmp_path, log).transcribe(long_audio, None)
    chunks = t.meta["chunks"]
    assert [(c["start"], c["end"]) for c in chunks] == [(0.0, 5.6), (5.6, 10.6), (10.6, 12.0)]
    assert t.ids() == [1, 2, 3, 4, 5, 6]
    assert [s.start for s in t.segments] == pytest.approx([0.5, 2.0, 6.1, 7.6, 11.1, 12.0])
    # the 2-9 s segment of each chunk is clamped to its chunk's end
    assert [s.end for s in t.segments] == pytest.approx([1.5, 5.6, 7.1, 10.6, 12.0, 12.0])
    assert t.meta["segments_clamped_to_chunk"] == 4
    assert all(c["engine"] == "gemini" and c["model"] == "m1" for c in chunks)
    assert t.meta["tokens"] == {"prompt": 30, "output": 15, "thinking": 0}
    assert t.meta["cost_usd_at_paid_prices"] == pytest.approx(0.03)
    assert t.meta["timestamps"] == "model-estimated"


def test_a_rerun_takes_every_chunk_from_the_cache(tmp_path, long_audio):
    log = []
    first = engine(tmp_path, log).transcribe(long_audio, None)
    calls = len(log)
    events = []
    second = engine(tmp_path, log, events=events).transcribe(long_audio, None)
    assert len(log) == calls  # no engine was called again
    assert [s.text for s in second.segments] == [s.text for s in first.segments]
    assert all(c["cached"] for c in second.meta["chunks"])
    assert [e.event for e in events].count("chunk-cached") == 3


def test_the_cache_key_changes_with_the_model_list(tmp_path, long_audio):
    log = []
    engine(tmp_path, log).transcribe(long_audio, None)
    engine(tmp_path, log, models=("m2",)).transcribe(long_audio, None)
    assert [name for name, _ in log].count("m2") == 3


def test_an_unavailable_model_passes_the_chunk_to_the_next(tmp_path, long_audio):
    log = []
    fail = {"m1": {2: GeminiUnavailable("Gemini m1 failed: 503 UNAVAILABLE", "m1")}}
    t = engine(tmp_path, log, fail).transcribe(long_audio, None)
    assert [c["model"] for c in t.meta["chunks"]] == ["m1", "m2", "m1"]
    assert "503" in t.meta["chunks"][1]["errors"][0]["error"]
    assert [name for name, _ in log] == ["m1", "m1", "m2", "m1"]


def test_whisper_takes_a_chunk_that_every_model_failed(tmp_path, long_audio):
    log = []
    fail = {"m1": {1: ValueError("malformed JSON")}, "m2": {1: RuntimeError("stopped with MAX_TOKENS")}}
    t = engine(tmp_path, log, fail).transcribe(long_audio, None)
    first = t.meta["chunks"][0]
    assert first["engine"] == "faster-whisper" and len(first["errors"]) == 2
    assert t.meta["chunk_engines"] == ["faster-whisper", "gemini"]
    assert t.segments[0].text == "whisper one"


def test_a_quota_error_stops_the_run_and_keeps_finished_chunks(tmp_path, long_audio):
    log = []
    fail = {"m1": {2: GeminiQuotaExhausted("Gemini m1 failed: 429 RESOURCE_EXHAUSTED (daily)", "m1")}}
    with pytest.raises(GeminiQuotaExhausted, match="Stopped at chunk 2 of 3"):
        engine(tmp_path, log, fail).transcribe(long_audio, None)
    assert [name for name, _ in log] == ["m1", "m1"]  # neither m2 nor whisper was tried
    assert len(list((tmp_path / "chunks").glob("chunk-001-*.json"))) == 1


def test_progress_events_cover_the_run(tmp_path, long_audio):
    events = []
    engine(tmp_path, [], events=events).transcribe(long_audio, None)
    kinds = [e.event for e in events]
    assert kinds[0] == "plan" and kinds[-1] == "done"
    assert kinds.count("chunk-start") == 3 and kinds.count("chunk-done") == 3
    done = [e for e in events if e.event == "chunk-done"]
    assert [e.chunk for e in done] == [1, 2, 3] and done[-1].audio_seconds_done == 12.0
    assert all(e.chunks == 3 and e.audio_seconds_total == 12.0 for e in events)
    assert "chunk 2/3" in done[1].line()


def test_the_default_gemini_engine_has_no_quota_fallback_of_its_own():
    t = ChunkedGeminiTranscriber(models=["a"])
    inner = t.gemini_factory("a")
    assert isinstance(inner, gemini.GeminiTranscriber) and inner.quota_fallback is False


def test_cache_only_reuses_gemini_chunks_and_calls_no_model(tmp_path, long_audio):
    log = []
    engine(tmp_path, log).transcribe(long_audio, None)  # all three chunks cached from Gemini
    for path in sorted((tmp_path / "chunks").glob("chunk-003-*.json")):
        path.unlink()  # chunk 3 never finished with Gemini
    log.clear()
    t = ChunkedGeminiTranscriber(models=["m1", "m2"], cache_dir=tmp_path / "chunks", cache_only=True,
                                 gemini_factory=lambda model: FakeEngine(model, log),
                                 whisper_factory=lambda: FakeEngine("whisper", log),
                                 chunk_seconds=5.0, search_seconds=1.0).transcribe(long_audio, None)
    assert [name for name, _ in log] == ["whisper"]
    assert [c["engine"] for c in t.meta["chunks"]] == ["gemini", "gemini", "faster-whisper"]
    assert t.meta["chunks"][2]["errors"][0]["error"] == "cache only: no Gemini call"
    assert t.meta["cache_only"] is True
    assert not list((tmp_path / "chunks").glob("chunk-003-*.json"))  # a later normal run asks Gemini again
