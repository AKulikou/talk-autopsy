"""The pipeline end to end on a three-segment transcript: fake STT, stub aligner, fake Claude."""

import re
from pathlib import Path

import pytest

from talk_autopsy import align as align_module
from talk_autopsy import pipeline
from talk_autopsy.correct import CorrectionAnswer
from talk_autopsy.correct import SegmentText as FixSegment
from talk_autopsy.glossary_auto import AutoGlossaryAnswer, AutoTerm
from talk_autopsy.segments import Segment, Transcript, load_transcript
from talk_autopsy.translate import SegmentText as RuSegment
from talk_autopsy.translate import TranslationAnswer
from tests.conftest import response
from tests.test_align import StubEmitter, spoken

TEXTS = ["One.", "Two.", "Three."]


class FakeSTT:
    name = "fake gemini"

    def __init__(self, calls):
        self.calls = calls

    def transcribe(self, audio, glossary):
        self.calls.append(audio)
        segments = [Segment(id=n, start=0.1 * n, end=0.1 * n + 0.1, text=t) for n, t in enumerate(TEXTS, 1)]
        return Transcript(segments=segments, language="en", source="fake gemini",
                          meta={"timestamps": "model-estimated", "cost_usd": 0.0, "audio_seconds": 2.0})


def claude_handler(kwargs):
    if kwargs["output_format"] is AutoGlossaryAnswer:
        term = AutoTerm(term="Three", heard_as=[], kind="product", keep=True, translation="", note="")
        return response(AutoGlossaryAnswer(terms=[term]))
    body = kwargs["messages"][0]["content"]
    found = re.findall(r"^\[(\d+)\] (.*)$", body.split("Segments to")[1], re.M)
    pairs = [(int(i), text) for i, text in found]
    if kwargs["output_format"] is CorrectionAnswer:
        return response(CorrectionAnswer(segments=[FixSegment(id=i, text=t) for i, t in pairs], edits=[]))
    return response(TranslationAnswer(segments=[RuSegment(id=i, text=f"перевод {i}") for i, _ in pairs]))


@pytest.fixture
def setup(monkeypatch, tmp_path, sine):
    stt_calls = []
    monkeypatch.setattr(pipeline, "make_stt",
                        lambda engine, cache_dir, progress, cache_only=False: FakeSTT(stt_calls))
    monkeypatch.setattr(align_module, "Wav2Vec2Emitter",
                        lambda: StubEmitter(spoken([(10, "one"), (40, "two"), (70, "three")])))
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test-key")
    glossary = tmp_path / "g.md"
    glossary.write_text("| term | keep |\n|---|---|\n| Two | no |\n", encoding="utf-8")
    steps = ["stt", "align", "fix", "translate", "srt"]
    settings = pipeline.RunSettings(source=sine, target_lang="ru", steps=steps, engine="gemini+align",
                                    glossary=glossary, work_root=tmp_path / "work")
    return settings, stt_calls


def test_all_steps_write_their_files(setup, fake_claude):
    settings, stt_calls = setup
    fake = fake_claude(claude_handler)
    events = []
    report = pipeline.run(settings, progress=events.append)
    work = settings.work_dir
    assert work.name == "sine"
    for name in ("audio.wav", "meta.json", "raw.json", "aligned.json", "fixed.json", "ru.json",
                 "sine.ru.srt", "sine.en.srt", "sine.ru.ass", "sine.en.ass"):
        assert (work / name).is_file(), name
    aligned = load_transcript(work / "aligned.json")
    assert aligned.meta["timestamps"] == "aligned" and aligned.segments[1].start == pytest.approx(0.8)
    ru = load_transcript(work / "ru.json")
    assert ru.ids() == [1, 2, 3] and ru.segments[0].text == "перевод 1"
    assert "перевод 2" in (work / "sine.ru.srt").read_text(encoding="utf-8")
    assert len(fake.requests) == 2 and len(stt_calls) == 1
    assert [s.step for s in report.steps] == ["stt", "align", "fix", "translate", "srt"]
    assert report.cost_usd == pytest.approx(2 * (1000 * 2 + 500 * 10) / 1e6)
    assert report.estimate_usd > 0
    kinds = {e.event for e in events}
    assert {"step-start", "step-done", "estimate", "done"} <= kinds
    assert any("Total" in line or "total" in line for line in pipeline.cost_lines(report))


def test_a_rerun_skips_every_step_that_is_up_to_date(setup, fake_claude):
    settings, stt_calls = setup
    fake = fake_claude(claude_handler)
    pipeline.run(settings)
    report = pipeline.run(settings)
    assert len(stt_calls) == 1 and len(fake.requests) == 2
    # srt is cheap: always written
    assert [s.skipped for s in report.steps] == [True, True, True, True, False]
    assert report.cost_usd == 0.0


def test_a_changed_glossary_reruns_the_claude_steps_only(setup, fake_claude):
    settings, stt_calls = setup
    fake = fake_claude(claude_handler)
    pipeline.run(settings)
    settings.glossary.write_text("| term | keep |\n|---|---|\n| Three | no |\n", encoding="utf-8")
    report = pipeline.run(settings)
    assert len(stt_calls) == 1 and len(fake.requests) == 4
    assert [s.skipped for s in report.steps[:4]] == [True, True, False, False]


def test_an_estimate_above_the_limit_stops_before_any_claude_request(setup, fake_claude):
    settings, _ = setup
    fake = fake_claude(claude_handler)
    settings.max_cost_usd = 0.000001
    with pytest.raises(pipeline.PipelineError, match="above the limit"):
        pipeline.run(settings)
    assert fake.requests == []


def test_a_step_without_its_input_says_which_step_to_run(setup, fake_claude):
    settings, _ = setup
    fake_claude(claude_handler)
    settings.steps = ["fix"]
    with pytest.raises(pipeline.PipelineError, match="run the step that writes it"):
        pipeline.run(settings)


# --- the default path: AssemblyAI, auto-glossary, key terms -----------------------------


class FakeAssemblyAI:
    def __init__(self, calls, fail=False):
        self.calls, self.fail = calls, fail

    def __call__(self, glossary_prompt):
        self.glossary_prompt = glossary_prompt
        return self

    def transcribe(self, audio, glossary):
        self.calls.append((Path(audio).name, self.glossary_prompt,
                           [e.term for e in glossary.entries] if glossary else None))
        if self.fail:
            raise RuntimeError("service unavailable")
        segments = [Segment(id=n, start=0.1 * n, end=0.1 * n + 0.1, text=t) for n, t in enumerate(TEXTS, 1)]
        return Transcript(segments=segments, language="en", source="assemblyai universal-3-5-pro",
                          meta={"timestamps": "measured", "cost_usd": 0.01, "audio_seconds": 2.0})


@pytest.fixture
def default_path(monkeypatch, tmp_path, sine):
    calls = []
    monkeypatch.setattr(pipeline, "make_assemblyai", FakeAssemblyAI(calls))
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "aai-test-key")
    glossary = tmp_path / "user.md"
    rows = ["| term | keep | notes |", "|---|---|---|", "| Two | yes | from the user |"]
    glossary.write_text(chr(10).join(rows) + chr(10), encoding="utf-8")
    settings = pipeline.RunSettings(source=sine, target_lang="ru", steps=list(pipeline.STEPS),
                                    glossary=glossary, context="Title: a test talk",
                                    work_root=tmp_path / "work")
    return settings, calls


def stub_aligner(monkeypatch):
    monkeypatch.setattr(align_module, "Wav2Vec2Emitter",
                        lambda: StubEmitter(spoken([(10, "one"), (40, "two"), (70, "three")])))


def test_default_path_runs_glossary_and_a_key_term_pass(default_path, fake_claude):
    settings, calls = default_path
    fake = fake_claude(claude_handler)
    report = pipeline.run(settings)
    work = settings.work_dir
    for name in ("audio.flac", "raw.json", "glossary.auto.md", "glossary.md", "raw2.json", "aligned.json",
                 "fixed.json", "ru.json", "sine.ru.srt", "sine.ru.ass", "sine.en.srt", "sine.en.ass"):
        assert (work / name).is_file(), name
    # both passes upload the compressed audio; the second gets the merged glossary as key terms
    assert calls == [("audio.flac", False, None), ("audio.flac", True, ["Two", "Three"])]
    assert [s.step for s in report.steps] == list(pipeline.STEPS)
    assert report.stt_cost_usd == pytest.approx(0.02) and report.fallbacks == []
    glossary_request, fix_request, translate_request = fake.requests
    assert glossary_request["model"] == "claude-haiku-5-5" and "fallbacks" not in glossary_request
    assert "Title: a test talk" in glossary_request["messages"][0]["content"]
    # run turns the refusal fallback on for the Sonnet stages
    assert fix_request["fallbacks"] == "default"
    assert translate_request["betas"] == ["server-side-fallback-2026-07-01"]
    assert "Title: a test talk" in fix_request["system"][0]["text"]
    assert load_transcript(work / "fixed.json").meta["correction"]["refusal_fallback"] is True
    assert set(report.subtitles) == {"sine.ru.srt", "sine.en.srt"}


def test_assemblyai_failure_falls_back_and_skips_the_key_term_pass(default_path, fake_claude, monkeypatch):
    settings, calls = default_path
    monkeypatch.setattr(pipeline, "make_assemblyai", FakeAssemblyAI(calls, fail=True))
    used = []

    def fallback_engine(engine, cache_dir, progress, cache_only=False):
        used.append(engine)
        return FakeSTT([])
    monkeypatch.setattr(pipeline, "make_stt", fallback_engine)
    stub_aligner(monkeypatch)
    fake_claude(claude_handler)
    events = []
    report = pipeline.run(settings, progress=events.append)
    assert used == ["gemini+align"] and len(calls) == 1  # no second AssemblyAI call
    raw = load_transcript(settings.work_dir / "raw.json")
    assert raw.meta["fallback"]["to"] == "gemini+align"
    assert "service unavailable" in raw.meta["fallback"]["reason"]
    assert "passed_through" in load_transcript(settings.work_dir / "raw2.json").meta
    assert load_transcript(settings.work_dir / "aligned.json").meta["timestamps"] == "aligned"
    assert any(e.event == "fallback" for e in events) and report.fallbacks


def test_without_a_gemini_key_the_fallback_is_faster_whisper(default_path, fake_claude, monkeypatch):
    settings, calls = default_path
    monkeypatch.setattr(pipeline, "make_assemblyai", FakeAssemblyAI(calls, fail=True))
    monkeypatch.setenv("GEMINI_API_KEY", "")
    used = []

    def fallback_engine(engine, cache_dir, progress, cache_only=False):
        used.append(engine)
        return FakeSTT([])
    monkeypatch.setattr(pipeline, "make_stt", fallback_engine)
    fake_claude(claude_handler)
    settings.steps = ["stt"]
    pipeline.run(settings)
    assert used == ["faster-whisper"]


def test_missing_assemblyai_key_fails_before_any_work(default_path, monkeypatch):
    from talk_autopsy.config import ConfigurationError
    settings, calls = default_path
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "")
    with pytest.raises(ConfigurationError, match="ASSEMBLYAI_API_KEY"):
        pipeline.run(settings)
    assert calls == [] and not settings.work_dir.exists()


def test_a_run_name_sets_the_work_folder_and_file_names(default_path, fake_claude):
    settings, _ = default_path
    fake_claude(claude_handler)
    settings.name, settings.steps = "real-01", ["stt", "srt"]
    pipeline.run(settings)
    assert settings.work_dir.name == "real-01" and (settings.work_dir / "real-01.en.srt").is_file()


# --- fix and translate commands ------------------------------------------------------


def _three_segments(tmp_path):
    from talk_autopsy.segments import save_transcript
    t = FakeSTT([]).transcribe(None, None)
    return save_transcript(t, tmp_path / "raw.json")


def test_fix_command_prints_the_estimate_and_writes_the_file(tmp_path, fake_claude):
    from typer.testing import CliRunner

    from talk_autopsy import cli
    fake_claude(claude_handler)
    out = tmp_path / "fixed.json"
    result = CliRunner().invoke(cli.app, ["fix", str(_three_segments(tmp_path)), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "Estimate: 1 block(s)" in result.output and "0 of 3 segments changed" in result.output
    assert load_transcript(out).meta["correction"]["prompt_version"] == "correction-v2"


def test_translate_command_stops_above_the_cost_limit(tmp_path, fake_claude):
    from typer.testing import CliRunner

    from talk_autopsy import cli
    fake = fake_claude(claude_handler)
    args = ["translate", str(_three_segments(tmp_path)), "--out", str(tmp_path / "ru.json"), "--lang", "ru",
            "--max-cost", "0.0000001"]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 1 and "above --max-cost" in result.output
    assert fake.requests == []
