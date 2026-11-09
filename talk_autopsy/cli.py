"""
Command line: `cut`, `transcribe`, `draft`, `ref-from-draft`, `eval` (work now)
and `run` (prints the plan).

Only argument parsing and printing live here; the stages are plain functions in
the package, so a web UI can call them the same way later.
"""

import re
from pathlib import Path
from typing import Annotated

import typer

from talk_autopsy import config, evaluation, ingest
from talk_autopsy.config import ConfigurationError
from talk_autopsy.glossary import GlossaryError, parse_glossary

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


FRAGMENT_ID = re.compile(r"^T\d+$")


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


@app.command(name="eval")
def eval_command(
    fragment: Annotated[str, typer.Argument(help="Test-set fragment ID, e.g. T2.")],
    hyp: Annotated[Path, typer.Option(help="Hypothesis: a Transcript JSON or a plain .txt.")],
    raw: Annotated[Path | None, typer.Option(
        help="Raw transcript before correction (JSON or .txt); adds the false-edit rate.")] = None,
    record: Annotated[bool, typer.Option(
        "--record", help="Append one row to results/benchmark.csv.")] = False,
    engine: Annotated[str | None, typer.Option(
        help="Engine or prompt version for the benchmark row (with --record).")] = None,
    stage: Annotated[str | None, typer.Option(
        help="Pipeline stage for the benchmark row, e.g. stt or fix (with --record).")] = None,
    notes: Annotated[str, typer.Option(help="Free text for the notes column (with --record).")] = "",
) -> None:
    """Score HYP against data/test-set/<FRAGMENT>.ref.txt: WER, term accuracy, false-edit rate."""
    if not FRAGMENT_ID.match(fragment):
        raise typer.BadParameter(f"expected an ID such as T2, got {fragment!r}", param_hint="FRAGMENT")
    if record and not (engine and engine.strip() and stage and stage.strip()):
        raise typer.BadParameter("--record needs --engine and --stage", param_hint="--record")
    ref_path = config.TEST_SET_DIR / f"{fragment}.ref.txt"
    if not ref_path.is_file():
        _fail(f"No reference transcript {ref_path}. Proofread the draft, then run "
              f"talk-autopsy ref-from-draft data/test-set/{fragment}.draft.txt --out {ref_path}")
    glossary_path = config.TEST_SET_DIR / f"{fragment}.glossary.md"
    try:
        reference = ref_path.read_text(encoding="utf-8")
        hypothesis = evaluation.read_transcript_text(hyp)
        raw_text = evaluation.read_transcript_text(raw).text if raw is not None else None
        glossary = None
        draft_glossary = False
        if glossary_path.is_file():
            glossary_text = glossary_path.read_text(encoding="utf-8")
            glossary = parse_glossary(glossary_text)
            draft_glossary = evaluation.is_draft_glossary(glossary_text)
        report = evaluation.evaluate(reference, hypothesis.text, glossary, raw_text)
    except (FileNotFoundError, ValueError, GlossaryError) as e:
        _fail(str(e))

    w = report.wer
    typer.echo(f"Fragment {fragment}: {hyp}")
    if hypothesis.meta:
        typer.echo(f"Run: {evaluation.describe_meta(hypothesis.meta)}")
    typer.echo(f"  {'WER':<16} {_percent(w.wer):>8}   (S {w.substitutions}, D {w.deletions}, "
               f"I {w.insertions}; {w.reference_words} reference words)")
    if report.terms is None:
        typer.echo(f"  {'term accuracy':<16} {'skipped':>8}   (no {glossary_path.name})")
    else:
        t = report.terms
        draft = "; glossary is a draft, not yet reviewed" if draft_glossary else ""
        typer.echo(f"  {'term accuracy':<16} {_percent(t.accuracy):>8}   "
                   f"({t.found}/{t.expected} occurrences of {len(t.rows)} terms{draft})")
        for row in t.rows:
            mark = "" if row.found == row.expected else "   <-"
            kind = "id" if row.identifier else ""
            typer.echo(f"      {row.term:<36} {kind:<3} {row.found}/{row.expected}{mark}")
        if t.absent:
            typer.echo(f"      not in the reference (excluded): {', '.join(t.absent)}")
    if report.false_edits is not None:
        f = report.false_edits
        typer.echo(f"  {'false-edit rate':<16} {_percent(f.rate):>8}   "
                   f"({f.false_edits} of {f.raw_hits} correct raw words changed)")

    if record:
        if draft_glossary:
            notes = "; ".join(n for n in (notes, "draft glossary") if n)
        row = evaluation.benchmark_row(report, fragment=fragment, stage=stage.strip(),
                                       engine=engine.strip(), meta=hypothesis.meta, notes=notes)
        try:
            evaluation.append_benchmark_row(config.BENCHMARK_CSV, row)
        except (FileNotFoundError, ValueError) as e:
            _fail(str(e))
        typer.echo(f"Recorded in {config.BENCHMARK_CSV}")
