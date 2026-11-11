"""
Two references of a fragment decided by the blind audio judge, and the spans
where they are uncertain (evaluation tooling for the T6 gate; decisions,
2026-11-10: the judge failed its T2 calibration as the sole adjudicator, so the
T6 gate uses a lower and an upper bound).

    work/T<n>/consensus.json + work/T<n>/judge.jsonl
      -> J: every judged span takes the judge's `final_text`        -> T<n>.ref.txt
      -> M: majority spans take the engines' majority variant,
            split spans the judge's `final_text`                    -> T<n>.ref.majority.txt
      -> uncertain spans: every split span (decided by the judge alone)
         and every majority span where J and M differ             -> work/T<n>/uncertain_spans.json

Every judged span's text in both references passes through the adjudication
normaliser (`judge.normalize_for_adjudication`). J and M differ only inside
uncertain spans: everything else is the same agreed words.

`uncertain_spans.json` holds span numbers, verdicts, reasons and each span's
character and word range in both references (words: tokens of
`metrics.tokenize`), plus the SHA-256 of both reference files, so that a
scoring run refuses ranges that belong to other reference files. It holds no
transcript text.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from talk_autopsy import adjudicate, judge, metrics
from talk_autopsy.adjudicate import NO_WORDS, Choice, Consensus, Resolution

VERSION = "two-references-v1"


class BoundsError(ValueError):
    """The judge's records or the reference files do not allow the two references or the bounds."""


def records_by_span(consensus: Consensus, records: list[dict]) -> dict[int, dict]:
    """One record per majority and split span, all by one model; anything else is an error."""
    by_span: dict[int, dict] = {}
    for record in records:
        if record["span"] in by_span:
            raise BoundsError(f"span {record['span']:04d} is judged twice in the records")
        by_span[record["span"]] = record
    models = {r.get("model") for r in records}
    if len(models) > 1:
        raise BoundsError(f"the records come from {len(models)} models; one model must judge every span")
    missing = [s.number for s in judge.judgeable(consensus) if s.number not in by_span]
    if missing:
        raise BoundsError(f"{len(missing)} spans are not judged yet (first: {missing[0]:04d}); "
                          "finish the judge run first")
    return by_span


def _typed(text: str) -> Choice:
    return Choice([], text or NO_WORDS)


def choices_j(consensus: Consensus, records: dict[int, dict]) -> dict[int, Choice]:
    """Reference J: the judge's `final_text` in every judged span."""
    return {s.number: _typed(records[s.number]["final_text"]) for s in judge.judgeable(consensus)}


def choices_m(consensus: Consensus, records: dict[int, dict]) -> dict[int, Choice]:
    """Reference M: the engines' majority variant where one exists, the judge's `final_text` on splits."""
    choices = {}
    for span in judge.judgeable(consensus):
        if span.verdict == "majority":
            choices[span.number] = Choice([span.options[span.premarked].label], "")
        else:
            choices[span.number] = _typed(records[span.number]["final_text"])
    return choices


def word_range(reference: str, chars: tuple[int, int]) -> tuple[int, int]:
    """Token range (start, end exclusive) of a character range, in `metrics.tokenize(reference)`."""
    start, end = chars
    first = len(metrics.tokenize(reference[:start]))
    return first, first + len(metrics.tokenize(reference[start:end]))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tokens(text: str) -> list[str]:
    # case kept: term accuracy is case-sensitive for identifiers
    return metrics.tokenize(text, lowercase=False)


def _ranges(resolution: Resolution, number: int) -> dict:
    chars = resolution.ranges[number]
    return {"chars": list(chars), "words": list(word_range(resolution.reference, chars))}


@dataclass
class TwoReferences:
    j: Resolution
    m: Resolution
    uncertain: list[dict]  # span, verdict, reason, ranges in J and M
    counts: dict


def build(consensus: Consensus, records: list[dict]) -> TwoReferences:
    """Both references, the uncertain spans and the counts (no text in the counts)."""
    by_span = records_by_span(consensus, records)
    normalize = judge.normalize_for_adjudication
    ref_j = adjudicate.resolve(consensus, choices_j(consensus, by_span), postprocess=normalize)
    ref_m = adjudicate.resolve(consensus, choices_m(consensus, by_span), postprocess=normalize)
    text_j = {row["number"]: row["text"] for row in ref_j.protocol["spans"]}
    text_m = {row["number"]: row["text"] for row in ref_m.protocol["spans"]}
    uncertain, overridden, differs_from_choice = [], 0, 0
    for span in judge.judgeable(consensus):
        n = span.number
        record = by_span[n]
        chosen = record["decision"]["text"]
        if record["choice"] != judge.NONE and _tokens(record["final_text"]) != _tokens(chosen):
            differs_from_choice += 1
        differs = _tokens(text_j[n]) != _tokens(text_m[n])
        if span.verdict == "majority" and differs:
            overridden += 1
        if span.verdict == "split" or differs:
            uncertain.append({
                "span": n, "verdict": span.verdict,
                "reason": "split: decided by the judge alone" if span.verdict == "split"
                else "majority: the judge's text differs from the engines' majority",
                "J": _ranges(ref_j, n),
                "M": _ranges(ref_m, n),
            })
    spans = judge.judgeable(consensus)
    wer = metrics.word_error_rate(ref_j.reference, ref_m.reference)
    counts = {
        "judged": {v: sum(1 for s in spans if s.verdict == v) for v in ("majority", "split")},
        "majority_overridden": overridden,
        "uncertain_spans": len(uncertain),
        "final_text_differs_from_chosen_option": differs_from_choice,
        "words_j": len(metrics.tokenize(ref_j.reference)), "words_m": len(metrics.tokenize(ref_m.reference)),
        "wer_j_m": {"wer": round(wer.wer, 4), "substitutions": wer.substitutions, "deletions": wer.deletions,
                    "insertions": wer.insertions, "reference_words": wer.reference_words},
    }
    return TwoReferences(ref_j, ref_m, uncertain, counts)


def uncertain_json(fragment: str, refs: TwoReferences, model: str, path_j: str, path_m: str,
                   date: str) -> str:
    data = {
        "fragment": fragment, "version": VERSION, "date": date, "judge_model": model,
        "reference_j": {"path": path_j, "sha256": sha256(refs.j.reference), "words": refs.counts["words_j"]},
        "reference_m": {"path": path_m, "sha256": sha256(refs.m.reference), "words": refs.counts["words_m"]},
        "note": ("word ranges are token ranges (start, end exclusive) of metrics.tokenize; "
                 "no transcript text here"),
        "counts": refs.counts,
        "spans": refs.uncertain,
    }
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def load_uncertain(path: Path, reference_j: str, reference_m: str) -> list[tuple[int, int]]:
    """The uncertain spans' word ranges in J, after checking that the file belongs to these references."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for key, text in (("reference_j", reference_j), ("reference_m", reference_m)):
        if data[key]["sha256"] != sha256(text):
            raise BoundsError(f"{path} was built for another {data[key]['path']}; rebuild it with "
                              f"talk-autopsy judge-refs {data['fragment']} --force")
    return [tuple(span["J"]["words"]) for span in data["spans"]]


def term_occurrences_inside(reference_j: str, terms: list[str],
                            ranges: list[tuple[int, int]]) -> tuple[int, int]:
    """(glossary-term occurrences in J as term_accuracy_v2 finds them, how many overlap an uncertain span)."""
    per_term = metrics.term_occurrences(reference_j, terms, possessive=True)
    found = [occ for occurrences in per_term for occ in occurrences]
    inside = sum(1 for occ in found if any(occ[0] < r[1] and r[0] < occ[1] for r in ranges))
    return len(found), inside
