"""The Windows launcher (scripts/windows): files that must stay consistent with the CLI."""

import re
from pathlib import Path

from talk_autopsy import outputs

WINDOWS = Path(__file__).resolve().parent.parent / "scripts" / "windows"
BAT = WINDOWS / "Talk Autopsy.bat"
PS1 = WINDOWS / "talk-autopsy.ps1"


def test_bat_calls_the_script_next_to_it_from_the_project_folder():
    text = BAT.read_bytes().decode("ascii")  # cmd reads the file in the OEM code page: ASCII only
    assert '"%~dp0talk-autopsy.ps1" %*' in text
    assert 'cd /d "%~dp0..\\.."' in text
    assert "-ExecutionPolicy Bypass" in text


def test_script_keeps_its_bom_for_windows_powershell():
    # Windows PowerShell 5.1 reads a BOM-less script as ANSI and garbles the Russian prompts.
    assert PS1.read_bytes().startswith(b"\xef\xbb\xbf")


def test_script_asks_for_formats_the_cli_knows():
    text = PS1.read_text(encoding="utf-8-sig")
    formats = re.search(r'^\$Formats = "([^"]+)"', text, re.MULTILINE).group(1)
    assert outputs.parse_outputs(formats) == ["srt", "ass", "mp4-burned"]


def test_script_finds_the_output_folder_line_the_cli_prints():
    text = PS1.read_text(encoding="utf-8-sig")
    assert "'^Outputs in (.+):$'" in text
    runs = Path(__file__).resolve().parent.parent / "talk_autopsy" / "cli" / "runs.py"
    cli = runs.read_text(encoding="utf-8")
    assert 'typer.echo(f"Outputs in {dest}:")' in cli


WEB_BAT = WINDOWS / "Talk Autopsy Web.bat"
SHORTCUTS = WINDOWS / "create-shortcut.ps1"


def test_web_bat_starts_the_page_from_the_project_folder():
    text = WEB_BAT.read_bytes().decode("ascii")  # cmd reads the file in the OEM code page: ASCII only
    assert "uv run --quiet talk-autopsy web %*" in text
    assert r'cd /d "%~dp0..\.."' in text


def test_the_main_shortcut_starts_the_web_page_and_the_console_one_stays():
    text = SHORTCUTS.read_text(encoding="utf-8")
    assert 'New-Shortcut "Talk Autopsy" (Join-Path $PSScriptRoot "Talk Autopsy Web.bat")' in text
    assert 'New-Shortcut "Talk Autopsy (console)" (Join-Path $PSScriptRoot "Talk Autopsy.bat")' in text
