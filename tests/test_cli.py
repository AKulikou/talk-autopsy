"""CLI argument parsing and the cut command; no network, no API."""

import wave

import pytest
from typer.testing import CliRunner

from talk_autopsy import cli, config

runner = CliRunner()


def test_cut_writes_the_requested_part(sine, tmp_path):
    out = tmp_path / "part.wav"
    args = ["cut", str(sine), "--start", "0:00.5", "--end", "1.5", "--out", str(out)]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    with wave.open(str(out)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        assert w.getnframes() / w.getframerate() == pytest.approx(1.0, abs=0.05)
    assert "Wrote" in result.output


def test_cut_rejects_a_bad_timecode(sine, tmp_path):
    result = runner.invoke(cli.app, ["cut", str(sine), "--end", "1:75", "--out", str(tmp_path / "x.wav")])
    assert result.exit_code == 2
    assert "below 60" in result.output


def test_cut_past_the_end_fails_cleanly(sine, tmp_path):
    result = runner.invoke(cli.app, ["cut", str(sine), "--end", "10:00", "--out", str(tmp_path / "x.wav")])
    assert result.exit_code == 1
    assert "past the end" in result.output


def test_run_dry_run_prints_the_plan(sine):
    result = runner.invoke(cli.app, ["run", str(sine), "--lang", "ru", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Target language: ru" in result.output
    for step in ("stt", "align", "fix", "translate", "srt"):
        assert step in result.output


def test_target_language_is_a_parameter(sine):
    args = ["run", str(sine), "--lang", "de", "--steps", "translate,srt", "--dry-run"]
    result = runner.invoke(cli.app, args)
    assert "Target language: de" in result.output
    assert "-> de.json" in result.output and ".de.srt" in result.output


def test_default_language_comes_from_config(sine, monkeypatch):
    monkeypatch.setattr(config, "TARGET_LANG", "uk")
    result = runner.invoke(cli.app, ["run", str(sine), "--steps", "stt", "--dry-run"])
    assert "Target language: uk" in result.output


@pytest.mark.parametrize("steps, expected", [
    ("srt,stt", ["stt", "srt"]),
    (" STT , fix ", ["stt", "fix"]),
    ("align,stt", ["stt", "align"]),
    ("dub", ["dub"]),
])
def test_steps_are_kept_in_pipeline_order(steps, expected):
    assert cli.parse_steps(steps) == expected


@pytest.mark.parametrize("args, message", [
    (["--steps", "stt,ocr"], "unknown step"),
    (["--steps", "stt,stt"], "listed twice"),
    (["--steps", ","], "at least one step"),
    (["--lang", "russian"], "ISO 639"),
    (["--engine", "vosk"], "unknown engine"),
    (["--steps", "dub"], "stage 4"),
])
def test_bad_arguments_are_usage_errors(sine, args, message):
    result = runner.invoke(cli.app, ["run", str(sine), *args])
    assert result.exit_code == 2
    assert message in result.output


def test_missing_local_file(tmp_path):
    result = runner.invoke(cli.app, ["run", str(tmp_path / "nope.mp4")])
    assert result.exit_code == 1
    assert "No such file" in result.output


def test_url_source_is_not_downloaded_yet():
    args = ["run", "https://example.com/watch?v=x", "--steps", "stt", "--engine", "faster-whisper"]
    result = runner.invoke(cli.app, args)
    assert "(URL)" in result.output and result.exit_code == 1
    assert "not implemented yet" in result.output


def test_claude_steps_fail_fast_without_a_key(sine, monkeypatch):
    monkeypatch.setenv(config.API_KEY_ENV_VAR, "")
    result = runner.invoke(cli.app, ["run", str(sine), "--steps", "stt,fix"])
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY is missing" in result.output


def test_gemini_engine_fails_fast_without_its_key(sine, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    result = runner.invoke(cli.app, ["run", str(sine), "--steps", "stt", "--engine", "gemini+align"])
    assert result.exit_code == 1 and "GEMINI_API_KEY is not set" in result.output


def test_steps_without_claude_need_no_key(sine, monkeypatch):
    monkeypatch.setenv(config.API_KEY_ENV_VAR, "")
    args = ["run", str(sine), "--steps", "stt,srt", "--engine", "faster-whisper", "--dry-run"]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0 and "API_KEY" not in result.output


def test_run_name_reaches_the_pipeline(sine, monkeypatch):
    from talk_autopsy import pipeline
    seen = {}

    def fake_run(settings, progress=None):
        seen["name"] = settings.name
        return pipeline.RunReport()
    monkeypatch.setattr(pipeline, "run", fake_run)
    result = runner.invoke(cli.app, ["run", str(sine), "--name", "real-01", "--steps", "srt"])
    assert result.exit_code == 0, result.output
    assert seen["name"] == "real-01"  # not the last step name printed in the plan
