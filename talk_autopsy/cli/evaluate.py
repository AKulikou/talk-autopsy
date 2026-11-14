"""Scoring a transcript against the test set: `eval`."""

from pathlib import Path
from typing import Annotated

import typer

from talk_autopsy import config, evaluation, metrics
from talk_autopsy.cli.common import FRAGMENT_ID, fail, majority_ref_path, percent, uncertain_spans_path
from talk_autopsy.glossary import GlossaryError, parse_glossary


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
    bounds: Annotated[bool, typer.Option(
        "--bounds", help="Score against both judge references (J and M) with the term-accuracy bounds, as "
                         "for T6 (needs judge-refs); a fragment that is not sealed also lists missed terms.")]
    = False,
) -> None:
    """Score HYP against data/test-set/<FRAGMENT>.ref.txt: WER, term accuracy, false-edit rate."""
    if not FRAGMENT_ID.match(fragment):
        raise typer.BadParameter(f"expected an ID such as T2, got {fragment!r}", param_hint="FRAGMENT")
    if record and not (engine and engine.strip() and stage and stage.strip()):
        raise typer.BadParameter("--record needs --engine and --stage", param_hint="--record")
    ref_path = config.TEST_SET_DIR / f"{fragment}.ref.txt"
    if not ref_path.is_file():
        fail(f"No reference transcript {ref_path}. Build it with talk-autopsy adjudicate {fragment} "
              f"..., decide the spans in data/test-set/{fragment}.adjudication.md, then run "
              f"talk-autopsy resolve {fragment}")
    glossary_path = config.TEST_SET_DIR / f"{fragment}.glossary.md"
    if fragment in config.SEALED_FRAGMENTS or bounds:
        _eval_sealed(fragment, ref_path, glossary_path, hyp, raw, record, engine, stage, notes,
                     sealed=fragment in config.SEALED_FRAGMENTS)
        return
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
        fail(str(e))

    w = report.wer
    typer.echo(f"Fragment {fragment}: {hyp}")
    if hypothesis.meta:
        typer.echo(f"Run: {evaluation.describe_meta(hypothesis.meta)}")
    typer.echo(f"  {'WER':<16} {percent(w.wer):>8}   (S {w.substitutions}, D {w.deletions}, "
               f"I {w.insertions}; {w.reference_words} reference words)")
    if report.terms is None:
        typer.echo(f"  {'term accuracy':<16} {'skipped':>8}   (no {glossary_path.name})")
    else:
        t = report.terms
        draft = "; glossary is a draft, not yet reviewed" if draft_glossary else ""
        typer.echo(f"  {'term accuracy':<16} {percent(t.accuracy):>8}   (v2, in place: "
                   f"{t.found}/{t.expected} occurrences of {len(t.rows)} terms{draft})")
        typer.echo(f"  {'  macro':<16} {percent(t.macro):>8}   (each term weighs the same)")
        typer.echo(f"  {'  without top':<16} {percent(t.micro_without_top):>8}   (without {t.top_term})")
        typer.echo(f"  {'  precision':<16} {percent(t.precision):>8}   ({t.false_positives} of "
                   f"{t.hypothesis_occurrences} term occurrences in the hypothesis not in place)")
        if report.terms_v1 is not None:
            typer.echo(f"  {'  v1 (old)':<16} {percent(report.terms_v1.accuracy):>8}   "
                       "(position-free, superseded)")
        for row in t.rows:
            mark = "" if row.found == row.expected else "   <-"
            extra = f"   +{row.false_positives} elsewhere" if row.false_positives else ""
            kind = "id" if row.identifier else ""
            typer.echo(f"      {row.term:<36} {kind:<3} {row.found}/{row.expected}{mark}{extra}")
        if t.absent:
            typer.echo(f"      not in the reference (excluded): {', '.join(t.absent)}")
    if report.false_edits is not None:
        f = report.false_edits
        typer.echo(f"  {'false-edit rate':<16} {percent(f.rate):>8}   ({f.false_edits} of {f.edits} word "
                   f"edits move the text away from the reference; {f.helpful_edits} move it closer)")

    if record:
        if draft_glossary:
            notes = "; ".join(n for n in (notes, "draft glossary") if n)
        row = evaluation.benchmark_row(report, fragment=fragment, stage=stage.strip(),
                                       engine=engine.strip(), meta=hypothesis.meta, notes=notes)
        try:
            evaluation.append_benchmark_row(config.BENCHMARK_CSV, row)
        except (FileNotFoundError, ValueError) as e:
            fail(str(e))
        typer.echo(f"Recorded in {config.BENCHMARK_CSV}")


def _eval_sealed(fragment: str, ref_path: Path, glossary_path: Path, hyp: Path, raw: Path | None,
                 record: bool, engine: str | None, stage: str | None, notes: str,
                 sealed: bool = True) -> None:
    """
    The sealed hold-out (T6), or any fragment with `--bounds` (T7): WER and term accuracy against both
    references (J: the judge's text, M: the engines' majority where one exists), the bounds from the
    uncertain spans and the gate verdict (metrics.bounded_term_accuracy). Sealed: counts only, no term,
    no transcript text; not sealed: the missed terms are listed too.
    """
    from talk_autopsy import ref_bounds

    majority_path, uncertain_path = majority_ref_path(fragment), uncertain_spans_path(fragment)
    for path, how in ((majority_path, f"talk-autopsy judge-refs {fragment}"),
                      (uncertain_path, f"talk-autopsy judge-refs {fragment}"),
                      (glossary_path, f"talk-autopsy glossary-ref {fragment}")):
        if not path.is_file():
            fail(f"No {path}; the sealed fragment is scored with both references and the bounds ({how})")
    try:
        reference_j = ref_path.read_text(encoding="utf-8")
        reference_m = majority_path.read_text(encoding="utf-8")
        ranges = ref_bounds.load_uncertain(uncertain_path, reference_j, reference_m)
        glossary = parse_glossary(glossary_path.read_text(encoding="utf-8"))
        hypothesis = evaluation.read_transcript_text(hyp)
        raw_text = evaluation.read_transcript_text(raw).text if raw is not None else None
        report = evaluation.evaluate(reference_j, hypothesis.text, glossary, raw_text)
        bounds = metrics.bounded_term_accuracy(reference_j, reference_m, hypothesis.text,
                                               [e.term for e in glossary.entries], ranges)
        wer_m = metrics.word_error_rate(reference_m, hypothesis.text)
    except (FileNotFoundError, ValueError, KeyError, GlossaryError) as e:
        fail(str(e))

    typer.echo(f"Fragment {fragment} ({'sealed: counts only' if sealed else 'two references and bounds'}): "
               f"{hyp}")
    if hypothesis.meta:
        typer.echo(f"Run: {evaluation.describe_meta(hypothesis.meta)}")
    for name, w in (("WER against J", report.wer), ("WER against M", wer_m)):
        typer.echo(f"  {name:<26} {percent(w.wer):>8}   (S {w.substitutions}, D {w.deletions}, "
                   f"I {w.insertions}; {w.reference_words} reference words)")
    for name, t, interval in (("term accuracy against J", bounds.against_j, bounds.interval_j),
                              ("term accuracy against M", bounds.against_m, bounds.interval_m)):
        typer.echo(f"  {name:<26} {percent(t.accuracy):>8}   ({t.found}/{t.expected} occurrences of "
                   f"{len(t.rows)} terms, in place; Wilson 95% {_interval(interval)}; macro "
                   f"{percent(t.macro)}; without the top term {percent(t.micro_without_top)}; "
                   f"precision {percent(t.precision)})")
    typer.echo(f"  {'term accuracy, lower bound':<26} {percent(bounds.lower):>8}   "
               f"({bounds.uncertain_occurrences} of {bounds.occurrences} occurrences inside uncertain "
               f"spans counted as misses; Wilson 95% {_interval(bounds.interval_lower)})")
    typer.echo(f"  {'term accuracy, upper bound':<26} {percent(bounds.upper):>8}   (counted as hits; "
               f"Wilson 95% {_interval(bounds.interval_upper)})")
    typer.echo(f"  gate: term accuracy >= {bounds.threshold:.0%}: {bounds.verdict.upper()}   "
               "(docs/experiments.md, T6 protocol)")
    t2 = _t2_reference_values()
    if t2:
        signal = metrics.overfitting_signal(bounds.against_j.accuracy or 0.0, t2)
        typer.echo(f"  overfitting signal: {'YES' if signal else 'no'}   "
                   f"(J {percent(bounds.against_j.accuracy)} against the T2 mean "
                   f"{percent(sum(t2) / len(t2))} of {len(t2)} pipeline-v3 repeats; "
                   f"signal if more than {metrics.OVERFITTING_MARGIN:.0%} below)")
    else:
        typer.echo("  overfitting signal: not computed (no current pipeline-v3 T2 rows in "
                   "results/benchmark.csv)")
    if report.false_edits is not None:
        f = report.false_edits
        typer.echo(f"  {'false-edit rate (J)':<26} {percent(f.rate):>8}   "
                   f"({f.false_edits} of {f.edits} word edits move the text away from J)")
    if not sealed:
        for name, t in (("J", bounds.against_j), ("M", bounds.against_m)):
            missed = [f"{row.term} {row.found}/{row.expected}" for row in t.rows if row.found < row.expected]
            typer.echo(f"  missed against {name}: {', '.join(missed) if missed else 'none'}")
    if record:
        m_terms = bounds.against_m.accuracy
        summary = (f"reference J; M: WER {wer_m.wer:.4f}, terms v2 "
                   f"{'n/a' if m_terms is None else f'{m_terms:.4f}'}; term bounds v2 "
                   f"[{bounds.lower:.4f}, {bounds.upper:.4f}], Wilson lower limit of the lower bound "
                   f"{bounds.interval_lower[0]:.4f}; gate {bounds.verdict}")
        notes = "; ".join(n for n in (notes, summary) if n)
        row = evaluation.benchmark_row(report, fragment=fragment, stage=stage.strip(), engine=engine.strip(),
                                       meta=hypothesis.meta, notes=notes)
        try:
            evaluation.append_benchmark_row(config.BENCHMARK_CSV, row)
        except (FileNotFoundError, ValueError) as e:
            fail(str(e))
        typer.echo(f"Recorded in {config.BENCHMARK_CSV}")


def _interval(interval: tuple[float, float]) -> str:
    return f"[{interval[0] * 100:.1f}%, {interval[1] * 100:.1f}%]"


PIPELINE_V3 = "pipeline-v3"


def _t2_reference_values() -> list[float]:
    """Term accuracy v2 (micro) of the current pipeline-v3 rows on T2: the overfitting comparison point."""
    import csv

    if not config.BENCHMARK_CSV.is_file():
        return []
    with config.BENCHMARK_CSV.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [float(r["term_accuracy_v2"]) for r in rows
            if r.get("fragment") == "T2" and r.get("engine_or_prompt") == PIPELINE_V3
            and r.get("status") == "current" and r.get("term_accuracy_v2")]
