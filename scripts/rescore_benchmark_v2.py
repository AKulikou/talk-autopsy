"""
Re-score the benchmark rows of T1 and T2 with term accuracy v2 and the one
false-edit definition (docs/experiments.md, Metrics; 2026-11-11). No API call:
every row is re-scored from the cached output it was recorded from.

    uv run python scripts/rescore_benchmark_v2.py [--check]

- Rows whose notes say "superseded" get status `superseded` and no v2 numbers
  (their glossary or reference was replaced; the current row of the same run
  follows them).
- Every other row gets status `current`; its hypothesis file is looked up in
  `ROW_FILES`, and before the v2 numbers are written the script recomputes WER
  and term accuracy v1 and stops if either differs from the recorded value:
  that proves the file is the one the row was scored from.
- Columns become `evaluation.BENCHMARK_COLUMNS`: `term_accuracy` and
  `false_edit_rate` are renamed `..._v1` (values kept), `timing_median_ms`
  (never measured) is dropped.

`--check` prints what would change and writes nothing.
"""

import csv
import io
import sys
from pathlib import Path

from talk_autopsy import config, evaluation
from talk_autopsy.glossary import parse_glossary

# (fragment, stage, engine_or_prompt) -> (hypothesis, raw before correction or None), in work/<fragment>/
ROW_FILES = {
    ("stt", "faster-whisper-large-v3"): ("raw.whisper-large-v3.json", None),
    ("stt", "assemblyai"): ("raw.assemblyai.json", None),
    ("stt", "gemini"): ("raw.gemini.json", None),
    ("stt", "assemblyai+keyterms"): ("raw.assemblyai+keyterms.json", None),
    ("stt", "faster-whisper-large-v3+glossary-prompt"): ("raw.whisper-large-v3+glossary-prompt.json", None),
    ("stt", "assemblyai+autoterms"): ("raw.assemblyai+autoterms.json", None),
    ("fix", "gemini+align+correction-v1"): ("fixed.gemini+align.correction-v1.json", "aligned.gemini.json"),
    ("fix", "gemini+align+correction-v1-opus"): ("fixed.gemini+align.correction-v1-opus.json",
                                                 "aligned.gemini.json"),
    ("fix", "faster-whisper-large-v3+correction-v1"): ("fixed.faster-whisper-large-v3.correction-v1.json",
                                                       "raw.whisper-large-v3.json"),
    ("fix", "assemblyai+autoterms+correction-v2"): ("fixed.assemblyai+autoterms.correction-v2.json",
                                                    "raw.assemblyai+autoterms.json"),
}
RENAMED = {"term_accuracy": "term_accuracy_v1", "false_edit_rate": "false_edit_rate_v1"}
DROPPED = {"timing_median_ms"}


def _fraction(value: float | None) -> str:
    return "" if value is None else f"{value:.4f}"


def rescore(row: dict[str, str]) -> dict[str, str]:
    out = {RENAMED.get(k, k): v for k, v in row.items() if k not in DROPPED}
    if "superseded" in row["notes"]:
        out["status"] = "superseded"
        return {c: out.get(c, "") for c in evaluation.BENCHMARK_COLUMNS}
    out["status"] = "current"
    hyp_name, raw_name = ROW_FILES[(row["stage"], row["engine_or_prompt"])]
    work = config.WORK_DIR / row["fragment"]
    reference = (config.TEST_SET_DIR / f"{row['fragment']}.ref.txt").read_text(encoding="utf-8")
    glossary_path = config.TEST_SET_DIR / f"{row['fragment']}.glossary.md"
    glossary = parse_glossary(glossary_path.read_text(encoding="utf-8"))
    hypothesis = evaluation.read_transcript_text(work / hyp_name).text
    raw = evaluation.read_transcript_text(work / raw_name).text if raw_name else None
    report = evaluation.evaluate(reference, hypothesis, glossary, raw)
    for column, value in (("wer", report.wer.wer), ("term_accuracy", report.terms_v1.accuracy)):
        if _fraction(value) != row[column]:
            raise SystemExit(f"{row['fragment']} {row['engine_or_prompt']}: {column} recomputes to "
                             f"{_fraction(value)}, recorded {row[column]}; not the scored file")
    if raw is not None and _fraction(report.false_edits_v1.rate) != row["false_edit_rate"]:
        raise SystemExit(f"{row['fragment']} {row['engine_or_prompt']}: false_edit_rate v1 recomputes to "
                         f"{_fraction(report.false_edits_v1.rate)}, recorded {row['false_edit_rate']}")
    t, f = report.terms, report.false_edits
    out.update({
        "term_accuracy_v2": _fraction(t.accuracy),
        "term_accuracy_v2_macro": _fraction(t.macro),
        "term_accuracy_v2_without_top": _fraction(t.micro_without_top),
        "term_precision": _fraction(t.precision),
        "term_false_positives": str(t.false_positives),
        "false_edit_rate_v2": _fraction(f.rate if f else None),
    })
    return {c: out.get(c, "") for c in evaluation.BENCHMARK_COLUMNS}


def main(argv: list[str]) -> int:
    check = "--check" in argv
    path = config.BENCHMARK_CSV
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if rows and "status" in rows[0]:
        print(f"{path} already has the v2 columns; nothing to do")
        return 0
    new_rows = [rescore(r) for r in rows]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(evaluation.BENCHMARK_COLUMNS), lineterminator="\n")
    writer.writeheader()
    writer.writerows(new_rows)
    for r in new_rows:
        if r["status"] == "current":
            print(f"{r['fragment']} {r['stage']:<4} {r['engine_or_prompt']:<42} "
                  f"v1 {r['term_accuracy_v1']} -> v2 {r['term_accuracy_v2']} "
                  f"(macro {r['term_accuracy_v2_macro']}, without top "
                  f"{r['term_accuracy_v2_without_top']}, precision {r['term_precision']}, false positives "
                  f"{r['term_false_positives']}); false-edit v1 {r['false_edit_rate_v1'] or '-'} -> v2 "
                  f"{r['false_edit_rate_v2'] or '-'}")
    if not check:
        Path(path).write_text(buffer.getvalue(), encoding="utf-8", newline="")
        print(f"wrote {path}: {len(new_rows)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
