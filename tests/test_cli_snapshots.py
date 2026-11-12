"""
Snapshots of the command line's visible behaviour: `--help` of the root command
and of every subcommand, and the output and exit code of `run --dry-run` and
`doctor`. Taken before cli.py was split into a package (v1.1), so the split can
be shown to change nothing a user sees.

Regenerate (only for a deliberate change, and say why in the commit):
    UPDATE_CLI_SNAPSHOTS=1 uv run pytest tests/test_cli_snapshots.py
"""

import datetime as dt
import os
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from talk_autopsy import cli, config, youtube

SNAPSHOTS = Path(__file__).parent / "snapshots" / "cli"
UPDATE = os.environ.get("UPDATE_CLI_SNAPSHOTS") == "1"
# A fixed, plain terminal: the same help layout on every machine and in CI.
ENV = {"COLUMNS": "100", "TERMINAL_WIDTH": "100", "NO_COLOR": "1", "TERM": "dumb", "FORCE_COLOR": None,
       "_TYPER_FORCE_DISABLE_TERMINAL": "1"}
runner = CliRunner()

COMMANDS = ["cut", "transcribe", "fix", "glossary-auto", "translate", "srt", "align", "run", "doctor", "web",
            "eval", "adjudicate", "resolve", "judge", "judge-compare", "judge-refs", "glossary-ref"]


@pytest.fixture(autouse=True)
def _fixed_help_layout(monkeypatch):
    """typer reads the width and the terminal flag once, when its help renderer is first imported."""
    from typer import rich_utils

    monkeypatch.setattr(rich_utils, "MAX_WIDTH", 100)
    monkeypatch.setattr(rich_utils, "FORCE_TERMINAL", False)


def _invoke(args: list[str]):
    return runner.invoke(cli.app, args, env=ENV)


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n")).strip() + "\n"


def _check(name: str, exit_code: int, output: str) -> None:
    actual = f"exit code: {exit_code}\n---\n{_normalize(output)}"
    path = SNAPSHOTS / f"{name}.txt"
    if UPDATE:
        SNAPSHOTS.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8", newline="\n")
        return
    assert path.is_file(), f"no snapshot {path.name}; run with UPDATE_CLI_SNAPSHOTS=1"
    assert actual == path.read_text(encoding="utf-8"), f"{name} differs from its snapshot"


def test_root_help():
    result = _invoke(["--help"])
    _check("help-root", result.exit_code, result.output)


def test_the_command_list_is_complete():
    names = {c.name or c.callback.__name__ for c in cli.app.registered_commands}
    assert names == set(COMMANDS)


@pytest.mark.parametrize("command", COMMANDS)
def test_command_help(command):
    result = _invoke([command, "--help"])
    _check(f"help-{command}", result.exit_code, result.output)


def _source_neutral(output: str, source: Path) -> str:
    """No machine paths: the source and the project's glossaries folder become placeholders."""
    return (output.replace(str(source), "<SOURCE>")
            .replace(str(config.GLOSSARIES_DIR / "web3.md"), "<GLOSSARIES>/web3.md"))


def test_run_dry_run_on_a_local_file(sine):
    result = _invoke(["run", str(sine), "--lang", "ru", "--dry-run"])
    _check("dry-run-local", result.exit_code, _source_neutral(result.output, sine))


def test_run_dry_run_with_a_domain_glossary_and_all_outputs(sine):
    result = _invoke(["run", str(sine), "--domain", "web3", "--output", "all", "--steps", "stt,fix,srt",
                      "--dry-run"])
    _check("dry-run-domain", result.exit_code, _source_neutral(result.output, sine))


def test_run_dry_run_on_a_missing_file(tmp_path):
    missing = tmp_path / "missing.mp4"
    result = _invoke(["run", str(missing), "--dry-run"])
    _check("dry-run-missing", result.exit_code, _source_neutral(result.output, missing))


def test_run_dry_run_with_an_unknown_step(sine):
    result = _invoke(["run", str(sine), "--steps", "stt,nope", "--dry-run"])
    # click's usage error box: keep only the message line, its frame differs between versions
    message = next((line for line in result.output.splitlines() if "unknown step" in line), result.output)
    _check("dry-run-unknown-step", result.exit_code, re.sub(r"[│|]", "", message).strip())


def _doctor(monkeypatch, *, ffmpeg: bool, keys: set[str]):
    real = youtube.doctor

    def fixed():
        return real(which=lambda tool: f"/usr/bin/{tool}" if ffmpeg else None,
                    runtimes=[("deno", "2.5.0")], version="2026.10.26", ejs=True, align=False,
                    key_is_set=lambda name: name in keys, today=dt.date(2026, 11, 12), web=True)

    monkeypatch.setattr(youtube, "doctor", fixed)


def test_doctor_all_required_present(monkeypatch):
    _doctor(monkeypatch, ffmpeg=True, keys={"ANTHROPIC_API_KEY", "ASSEMBLYAI_API_KEY"})
    result = _invoke(["doctor"])
    _check("doctor-ok", result.exit_code, result.output)


def test_doctor_missing_ffmpeg_fails(monkeypatch):
    _doctor(monkeypatch, ffmpeg=False, keys=set())
    result = _invoke(["doctor"])
    _check("doctor-missing", result.exit_code, result.output)
