"""
Command line: `talk-autopsy cut` (works now) and `talk-autopsy run` (prints the plan).

Only argument parsing and printing live here; the stages are plain functions in
the package, so a web UI can call them the same way later.
"""

from pathlib import Path
from typing import Annotated

import typer

from talk_autopsy import config, ingest
from talk_autopsy.config import ConfigurationError

app = typer.Typer(
    help="English technical talks to subtitles in a target language.",
    no_args_is_help=True,
    add_completion=False,
)

# Pipeline order; `run --steps` takes any subset and keeps this order.
STEPS: dict[str, str] = {
    "stt": "speech recognition (faster-whisper large-v3, CPU) -> raw.json",
    "fix": "Claude correction ({correction}) -> fixed.json",
    "translate": "Claude translation ({translation}) -> {lang}.json",
    "srt": "subtitles -> .{lang}.srt and .en.srt",
    "dub": "dubbed audio (edge-tts + ffmpeg) -> .{lang}.dub.m4a",
}
CLAUDE_STEPS = {"fix", "translate"}
DEFAULT_STEPS = "stt,fix,translate,srt"


def parse_steps(value: str) -> list[str]:
    """Comma-separated step names -> the same steps in pipeline order."""
    names = [name.strip().lower() for name in value.split(",") if name.strip()]
    if not names:
        raise typer.BadParameter("give at least one step")
    unknown = [name for name in names if name not in STEPS]
    if unknown:
        raise typer.BadParameter(f"unknown step(s) {', '.join(unknown)}; known: {', '.join(STEPS)}")
    if len(set(names)) != len(names):
        raise typer.BadParameter(f"a step is listed twice: {value}")
    return [name for name in STEPS if name in names]


def is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def _fail(message: str) -> None:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _timecode(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return ingest.parse_timecode(value)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from None


@app.command()
def cut(
    source: Annotated[Path, typer.Argument(help="Video or audio file.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="WAV file to write.")],
    start: Annotated[str | None, typer.Option(help="SS, MM:SS or HH:MM:SS. Default: the beginning.")] = None,
    end: Annotated[str | None, typer.Option(help="SS, MM:SS or HH:MM:SS. Default: the end.")] = None,
) -> None:
    """Extract 16 kHz mono WAV audio from SOURCE, optionally only from --start to --end."""
    begin, finish = _timecode(start), _timecode(end)
    try:
        ingest.extract_audio(source, out, begin, finish)
        seconds = ingest.probe(out)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        _fail(str(e))
    typer.echo(f"Wrote {out} ({seconds:.1f} s, {ingest.SAMPLE_RATE} Hz mono)")


@app.command()
def run(
    source: Annotated[str, typer.Argument(help="Video URL or local file.")],
    lang: Annotated[str | None, typer.Option(help="Target language code. Default: TARGET_LANG.")] = None,
    steps: Annotated[str, typer.Option(help=f"Comma-separated, any of: {', '.join(STEPS)}.")] = DEFAULT_STEPS,
) -> None:
    """Run the pipeline on SOURCE. Not implemented yet: prints the plan and stops."""
    selected = parse_steps(steps)
    try:
        target_lang = config.check_lang(lang if lang is not None else config.TARGET_LANG)
    except ConfigurationError as e:
        raise typer.BadParameter(str(e), param_hint="--lang") from None
    if not is_url(source) and not Path(source).is_file():
        _fail(f"No such file: {source}")

    typer.echo(f"Source: {source} ({'URL' if is_url(source) else 'local file'})")
    typer.echo(f"Target language: {target_lang}")
    typer.echo("Steps:")
    for number, name in enumerate(selected, start=1):
        detail = STEPS[name].format(
            correction=config.CORRECTION_MODEL, translation=config.TRANSLATION_MODEL, lang=target_lang
        )
        typer.echo(f"  {number}. {name:<10} {detail}")

    if CLAUDE_STEPS & set(selected):
        try:
            config.require_api_key()  # fail before any work, not after speech recognition
        except ConfigurationError as e:
            _fail(str(e))
    typer.secho("Not implemented yet: the stages arrive with stage 1 and later.",
                fg=typer.colors.YELLOW, err=True)
    raise typer.Exit(1)
