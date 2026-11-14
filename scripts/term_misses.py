"""
Missed reference-glossary occurrences of term accuracy v2, one row per occurrence, with
the pipeline's form, the forms in references J and M, and a cause. A diagnostic for
docs/experiments.md (T7 baseline result); it changes no reference, glossary or metric.

    uv run python scripts/term_misses.py T7 <run>/fixed.json [<run>/fixed.json ...] --names b1 b2 b3 a

Occurrences and hits come from `metrics.term_accuracy_v2` against J (the metric of the
T7 rule). The forms are the tokens aligned to the occurrence (`metrics._alignment`, the
metric's own word alignment): the pipeline's tokens for J's, and M's tokens for J's. Causes,
in this order:

- `position`: the pipeline has the term in exactly this form at the aligned place, or within
  5 words of it, but not aligned token for token (an insertion or deletion next to it);
- `form`: the same letters and digits in another written form; the kind is named
  (case, hyphen, underscore, space, dot, digits apart), or the term written into one token
  with a neighbour word (`joined to a neighbour word`);
- `reference doubt`: the pipeline has the language's own code form where J has the spoken
  form (CODE_FORMS: forms fixed by the language, not by what was on screen);
- `recognition`: anything else (other words).

`code form` says who matches the language's convention where the language fixes it
(CODE_FORMS): `pipeline` or `reference`; empty where it depends on the screen.
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from talk_autopsy import config, metrics
from talk_autopsy.glossary import load_glossary
from talk_autopsy.segments import load_transcript

# Forms the language fixes, whatever was on screen: Solidity globals and built-ins, a Make special target.
CODE_FORMS = {"msg.sender", "msg.value", ".PHONY", "string.concat", "pragma solidity", "abi.encodePacked"}
SPOKEN = {"message.sender": "msg.sender", "message.value": "msg.value", "Phony": ".PHONY"}
NEAR = 5


def _letters(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.casefold())


def _form_kind(pipeline: str, reference: str) -> str:
    kinds = []
    joined_p, joined_r = pipeline.replace(" ", ""), reference.replace(" ", "")
    if joined_p.casefold() == joined_r.casefold() and pipeline != reference:
        kinds.append("case" if pipeline.replace(" ", "") != reference.replace(" ", "") else "")
    for mark, name in (("-", "hyphen"), ("_", "underscore"), (".", "dot"), ("(", "brackets")):
        if (mark in pipeline) != (mark in reference):
            kinds.append(name)
    if (" " in pipeline) != (" " in reference):
        kinds.append("space")
    if re.search(r"[A-Za-z] \d", pipeline + " " + reference):
        kinds.append("digits apart")
    if not kinds or kinds == [""]:
        kinds = ["case"]
    return ", ".join(k for k in dict.fromkeys(kinds) if k)


MAX_SPAN = 8


def _aligned(pairs: dict[int, int], start: int, end: int,
             other: list[str]) -> tuple[list[str], int, list[str]]:
    """
    The other text's tokens between the tokens aligned to the reference words just before and just
    after [start, end): the substituted words and any inserted next to them (`Mood NFT` for `MoodNFT`);
    their start index; and the two aligned neighbour tokens (a term written into a neighbour word).
    """
    before = [pairs[k] for k in range(start - 1, max(-1, start - 50), -1) if k in pairs][:1]
    after = [pairs[k] for k in range(end, end + 50) if k in pairs][:1]
    lo = before[0] + 1 if before else 0
    hi = after[0] if after else len(other)
    neighbours = [other[i] for i in (before + after) if 0 <= i < len(other)]
    return other[lo:hi], lo, neighbours


def _shown(tokens: list[str]) -> str:
    if not tokens:
        return "(nothing)"
    return " ".join(tokens[:MAX_SPAN]) + (" …" if len(tokens) > MAX_SPAN else "")


def _classify(span: list[str], neighbours: list[str], j_form: str) -> tuple[str, str]:
    """(cause, the pipeline form shown) for a missed occurrence; `position` is decided by the caller."""
    target = _letters(j_form)
    spoken = SPOKEN.get(j_form)
    for k in range(len(span)):  # the shortest stretch of the span that carries the same letters
        for m in range(k + 1, len(span) + 1):
            text = " ".join(span[k:m])
            if spoken and _letters(spoken) in _letters(text):
                return "reference doubt", text
            if _letters(text) == target:
                return f"form ({_form_kind(text, j_form)})", text
    for token in span + neighbours:  # the term written into one token with a neighbour word
        if target and target in _letters(token) and _letters(token) != target:
            return "form (joined to a neighbour word)", token
    if not span and neighbours:  # nothing of its own: show the neighbour that shares most of its letters
        last = _letters(j_form.split()[-1])
        best = max(neighbours, key=lambda t: (last[-4:] in _letters(t), len(set(_letters(t)) & set(target))))
        return "recognition", f"(inside) {best}"
    return "recognition", _shown(span)


def misses(reference_j: str, reference_m: str, hypothesis: str, glossary) -> list[dict]:
    terms = [e.term for e in glossary.entries]
    category = {e.term: e.notes for e in glossary.entries}
    result = metrics.term_accuracy_v2(reference_j, hypothesis, terms)
    ref = metrics.tokenize(reference_j, lowercase=False)
    hyp = metrics.tokenize(hypothesis, lowercase=False)
    ref_m = metrics.tokenize(reference_m, lowercase=False)
    to_hyp = metrics._alignment(ref, hyp)
    to_m = metrics._alignment(ref, ref_m)
    hyp_found = metrics.term_occurrences(hypothesis, terms, possessive=True)
    rows = []
    for index, term in enumerate(terms):
        for number, ((start, end), hit) in enumerate(zip(result.occurrences[index], result.hits[index],
                                                         strict=True), start=1):
            if hit:
                continue
            j_form = " ".join(ref[start:end])
            span, p_at, neighbours = _aligned(to_hyp, start, end, hyp)
            m_span, _, _ = _aligned(to_m, start, end, ref_m)
            m_form = _shown(m_span)
            near = any(abs(s - p_at) <= NEAR for s, _ in hyp_found[index])
            if near or " ".join(span) == j_form:
                cause, p_form = "position", _shown(span)
            else:
                cause, p_form = _classify(span, neighbours, j_form)
            code = ""
            if cause == "reference doubt":
                code = "pipeline"
            elif j_form in CODE_FORMS and p_form != j_form:
                code = "reference"
            rows.append({"term": term, "number": number, "category": category.get(term, ""),
                         "pipeline": p_form, "J": j_form, "M": m_form, "cause": cause, "code": code})
    return rows


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("fragment")
    parser.add_argument("fixed", nargs="+", type=Path)
    parser.add_argument("--names", nargs="+", required=True)
    args = parser.parse_args(argv)
    if len(args.names) != len(args.fixed):
        parser.error("give one name per run")
    test_set = config.TEST_SET_DIR
    ref_j = (test_set / f"{args.fragment}.ref.txt").read_text(encoding="utf-8")
    ref_m = (test_set / f"{args.fragment}.ref.majority.txt").read_text(encoding="utf-8")
    glossary = load_glossary(test_set / f"{args.fragment}.glossary.md")
    by_occurrence: dict[tuple[str, int], dict] = {}
    counts: dict[str, Counter] = {}
    for name, fixed in zip(args.names, args.fixed, strict=True):
        hypothesis = " ".join(s.text for s in load_transcript(fixed).segments)
        counts[name] = Counter()
        for row in misses(ref_j, ref_m, hypothesis, glossary):
            counts[name][row["cause"].split(" (")[0]] += 1
            key = (row["term"], row["number"])
            entry = by_occurrence.setdefault(key, {**row, "runs": {}})
            entry["runs"][name] = (row["pipeline"], row["cause"])
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("| term (occurrence) | category | pipeline form (runs) | J form | M form | cause | code form |")
    print("|---|---|---|---|---|---|---|")
    order = {e.term: k for k, e in enumerate(glossary.entries)}
    for (term, number), e in sorted(by_occurrence.items(), key=lambda kv: (order[kv[0][0]], kv[0][1])):
        forms: dict[str, list[str]] = {}
        for name, (form, _) in e["runs"].items():
            forms.setdefault(form, []).append(name)
        pipeline = "; ".join(f"`{f}` ({', '.join(ns)})" for f, ns in forms.items())
        causes = sorted({c for _, c in e["runs"].values()})
        print(f"| `{term}` ({number}) | {e['category']} | {pipeline} | `{e['J']}` | `{e['M']}` | "
              f"{'; '.join(causes)} | {e['code']} |")
    print()
    kinds = ["form", "reference doubt", "position", "recognition"]
    print("| run | missed | " + " | ".join(kinds) + " |")
    print("|---|---|" + "---|" * len(kinds))
    for name in args.names:
        c = counts[name]
        print(f"| {name} | {sum(c.values())} | " + " | ".join(str(c[k]) for k in kinds) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
