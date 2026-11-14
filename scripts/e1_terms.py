"""
E1 guard (docs/experiments.md, E1): term accuracy of T7 runs against reference J with form
normalization (talk_autopsy/term_forms.py), next to the frozen metric against J and M.

    uv run python scripts/e1_terms.py work/T7/runs/T7-b-r1 work/T7/runs/T7-b-r2 work/T7/runs/T7-b-r3

Each argument is a run folder with `fixed.json` (the corrected transcript, as `eval T7 --hyp`).
Prints numbers only: no term, no reference text.
"""

import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    from talk_autopsy import config, evaluation, metrics, term_forms
    from talk_autopsy.cli.common import majority_ref_path
    from talk_autopsy.glossary import parse_glossary

    runs = [Path(p) for p in sys.argv[1:]]
    if not runs:
        sys.exit(__doc__)
    reference_j = (config.TEST_SET_DIR / "T7.ref.txt").read_text(encoding="utf-8")
    reference_m = majority_ref_path("T7").read_text(encoding="utf-8")
    glossary = parse_glossary((config.TEST_SET_DIR / "T7.glossary.md").read_text(encoding="utf-8"))
    terms = [e.term for e in glossary.entries]
    rows = []
    for run in runs:
        hyp = evaluation.read_transcript_text(run / "fixed.json").text
        j = metrics.term_accuracy_v2(reference_j, hyp, terms)
        m = metrics.term_accuracy_v2(reference_m, hyp, terms)
        jn = term_forms.term_accuracy(reference_j, hyp, terms)
        mn = term_forms.term_accuracy(reference_m, hyp, terms)
        rows.append((jn.accuracy, j.accuracy, m.accuracy, mn.accuracy))
        hits = sum(sum(h) for h in jn.hits)
        total = sum(len(h) for h in jn.hits)
        print(f"{run.name:<14} J normalized {100 * jn.accuracy:6.2f}% ({hits}/{total})  "
              f"J {100 * j.accuracy:6.2f}%  M {100 * m.accuracy:6.2f}%  "
              f"M normalized {100 * mn.accuracy:6.2f}%")
    if len(rows) > 1:
        means = [statistics.mean(r[k] for r in rows) for k in range(4)]
        print(f"{'mean':<14} J normalized {100 * means[0]:6.2f}%  J {100 * means[1]:6.2f}%  "
              f"M {100 * means[2]:6.2f}%  M normalized {100 * means[3]:6.2f}%")


if __name__ == "__main__":
    main()
