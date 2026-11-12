"""Helpers every command module uses: errors, logging, progress lines, shared options, test-set paths."""

import logging
import re
import sys
from pathlib import Path
from typing import Annotated

import typer

from talk_autopsy import blocks, config, ingest
from talk_autopsy.glossary import GlossaryError, load_glossary, parse_glossary
from talk_autopsy.segments import load_transcript


def fail(message: str) -> None:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def timecode(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return ingest.parse_timecode(value)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from None


def log_to_stderr() -> None:
    """Progress of long stages (model download, transcription) goes to stderr with timestamps."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
                        stream=sys.stderr, force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per Hugging Face request otherwise


def print_progress(event) -> None:
    typer.echo(event.line(), err=True)


ForceOption = Annotated[bool, typer.Option("--force", help="Overwrite OUT if it exists.")]


def stage_inputs(transcript: Path, glossary: Path | None, context: Path | None):
    try:
        source = load_transcript(transcript)
        terms = load_glossary(glossary) if glossary is not None else parse_glossary("")
    except (FileNotFoundError, ValueError, GlossaryError) as e:
        fail(str(e))
    return source, terms, blocks.load_context(context)


def confirm_cost(estimate, max_cost: float) -> None:
    typer.echo(f"Estimate: {estimate.line()}")
    if estimate.usd > max_cost:
        fail(f"the estimate is above --max-cost {max_cost:.2f} USD; raise it to confirm")


def write_new(out: Path, text: str, force: bool) -> None:
    """Write `text` to `out`; an existing file (maybe hours of hand edits) needs --force."""
    if out.exists() and not force:
        fail(f"{out} exists; it may hold hand edits. Pass --force to overwrite it.")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")


FRAGMENT_ID = re.compile(r"^T\d+$")


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def check_fragment(fragment: str) -> str:
    if not FRAGMENT_ID.match(fragment):
        raise typer.BadParameter(f"expected an ID such as T2, got {fragment!r}", param_hint="FRAGMENT")
    return fragment


def consensus_path(fragment: str) -> Path:
    return config.WORK_DIR / fragment / "consensus.json"


def judged_path(fragment: str) -> Path:
    return config.TEST_SET_DIR / f"{fragment}.adjudication.judged.md"


def majority_ref_path(fragment: str) -> Path:
    return config.TEST_SET_DIR / f"{fragment}.ref.majority.txt"


def uncertain_spans_path(fragment: str) -> Path:
    return config.WORK_DIR / fragment / "uncertain_spans.json"
