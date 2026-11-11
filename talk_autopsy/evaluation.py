"""
Evaluation of one transcript against a test-set reference, and the benchmark row.

    hypothesis (Transcript JSON or .txt) + T<n>.ref.txt [+ T<n>.glossary.md] [+ raw]
      -> EvalReport -> one row of results/benchmark.csv

The metrics themselves are pure functions in metrics.py; this module reads the
files and writes the CSV row. Printing is left to the CLI.
"""

import csv
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from talk_autopsy import metrics
from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Transcript

DRAFT_MARKER = "<!-- draft"  # first line of a glossary nobody has reviewed yet


@dataclass(frozen=True)
class TextFile:
    """The text of a transcript file and, for a Transcript JSON, its meta."""

    text: str
    meta: dict


def read_transcript_text(path: Path) -> TextFile:
    """
    A `Transcript` JSON (segment texts joined with spaces) or any other file read
    as plain UTF-8 text.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"No such file: {path}")
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        try:
            transcript = Transcript.model_validate_json(raw)
        except ValueError as e:
            raise ValueError(f"{path} is not a Transcript JSON: {e}") from None
        return TextFile(" ".join(s.text.strip() for s in transcript.segments), transcript.meta)
    return TextFile(raw, {})


def is_draft_glossary(text: str) -> bool:
    lines = text.lstrip().splitlines()
    return bool(lines) and lines[0].strip().startswith(DRAFT_MARKER)


# The columns of results/benchmark.csv, in order. Rates are fractions with four decimals.
# term_accuracy_v1 and false_edit_rate_v1 are the superseded measures, kept for the record
# (docs/experiments.md, Metrics); `status` is current or superseded (the reason in `notes`).
BENCHMARK_COLUMNS = (
    "date", "fragment", "stage", "engine_or_prompt", "status", "wer",
    "term_accuracy_v2", "term_accuracy_v2_macro", "term_accuracy_v2_without_top", "term_precision",
    "term_false_positives", "false_edit_rate_v2", "term_accuracy_v1", "false_edit_rate_v1",
    "cost_usd", "minutes", "notes",
)


@dataclass(frozen=True)
class EvalReport:
    wer: metrics.WerResult
    terms: metrics.TermAccuracyV2 | None  # the benchmark's term accuracy; None without a glossary
    false_edits: metrics.FalseEditResult | None  # None without a raw transcript
    terms_v1: metrics.TermAccuracyResult | None = None  # superseded, for the record
    false_edits_v1: metrics.FalseEditResultV1 | None = None  # superseded, for the record


def evaluate(reference: str, hypothesis: str, glossary: Glossary | None = None,
             raw: str | None = None) -> EvalReport:
    """WER always; term accuracy (v2, v1) with a glossary; false-edit rate (v2, v1) with the raw text."""
    terms = terms_v1 = None
    if glossary is not None:
        names = [e.term for e in glossary.entries]
        terms = metrics.term_accuracy_v2(reference, hypothesis, names)
        terms_v1 = metrics.term_accuracy_v1(reference, hypothesis, names)
    false_edits = false_edits_v1 = None
    if raw is not None:
        tokens = metrics.tokenize(raw), metrics.tokenize(hypothesis), metrics.tokenize(reference)
        false_edits = metrics.false_edit_rate(*tokens)
        false_edits_v1 = metrics.false_edit_rate_v1(*tokens)
    return EvalReport(metrics.word_error_rate(reference, hypothesis), terms, false_edits, terms_v1,
                      false_edits_v1)


def _fraction(value: float | None) -> str:
    return "" if value is None else f"{value:.4f}"


def benchmark_row(report: EvalReport, *, fragment: str, stage: str, engine: str, meta: dict,
                  notes: str = "", date: dt.date | None = None, status: str = "current") -> dict[str, str]:
    """
    One row of results/benchmark.csv (`BENCHMARK_COLUMNS`). Rates are fractions
    with four decimals. `minutes` and `cost_usd` come from the hypothesis meta
    (`wall_seconds`, `cost_usd`) and stay empty when it does not record them: no
    number that was not measured.
    """
    wall = meta.get("wall_seconds")
    cost = meta.get("cost_usd")
    t, f = report.terms, report.false_edits
    return {
        "date": (date or dt.date.today()).isoformat(),
        "fragment": fragment,
        "stage": stage,
        "engine_or_prompt": engine,
        "status": status,
        "wer": _fraction(report.wer.wer),
        "term_accuracy_v2": _fraction(t.accuracy if t else None),
        "term_accuracy_v2_macro": _fraction(t.macro if t else None),
        "term_accuracy_v2_without_top": _fraction(t.micro_without_top if t else None),
        "term_precision": _fraction(t.precision if t else None),
        "term_false_positives": "" if t is None else str(t.false_positives),
        "false_edit_rate_v2": _fraction(f.rate if f else None),
        "term_accuracy_v1": _fraction(report.terms_v1.accuracy if report.terms_v1 else None),
        "false_edit_rate_v1": _fraction(report.false_edits_v1.rate if report.false_edits_v1 else None),
        "cost_usd": "" if cost is None else f"{float(cost):.4f}",
        "minutes": "" if wall is None else f"{float(wall) / 60:.1f}",
        "notes": notes,
    }


def append_benchmark_row(csv_path: Path, row: dict[str, str]) -> None:
    """
    Append `row` under the existing header. The file must exist and its header
    must have exactly the row's columns (in any order); the header is never
    rewritten here.
    """
    csv_path = Path(csv_path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"No benchmark table at {csv_path}")
    with csv_path.open(encoding="utf-8", newline="") as f:
        header = next(csv.reader(f), None)
    if not header or set(header) != set(row):
        raise ValueError(f"{csv_path}: header {header} does not match the row columns {sorted(row)}")
    content = csv_path.read_bytes()
    # Keep the file's own line ending: a Windows checkout may have CRLF.
    newline = "\r\n" if content.split(b"\n", 1)[0].endswith(b"\r") else "\n"
    with csv_path.open("a", encoding="utf-8", newline="") as f:
        if not content.endswith(b"\n"):
            f.write(newline)
        csv.DictWriter(f, fieldnames=header, lineterminator=newline).writerow(row)


def describe_meta(meta: dict) -> str:
    """Short one-line summary of the run details in a Transcript meta, for printing."""
    keys = ("engine", "model", "compute_type", "glossary_prompt", "wall_seconds", "realtime_factor")
    return json.dumps({k: meta[k] for k in keys if k in meta}, ensure_ascii=False)
