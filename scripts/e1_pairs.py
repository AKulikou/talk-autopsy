"""
E1 decision (docs/experiments.md, E1): a blind pairwise comparison of the translations of two runs.

    uv run python scripts/e1_pairs.py sample <run A> <run B> --dict-a glossaries/web3.md \
        --dict-b glossaries/web3-v2.md [--seed 20261116 --order-seed 20261117 --max 20]
    uv run python scripts/e1_pairs.py count <run A> <run B> --dict-a ... --dict-b ...
    uv run python scripts/e1_pairs.py score

`sample` takes the **eligible** sentence units (`ru.json`; amendment of 2026-11-14, before any v2
run): the English is the same in both runs (tokens as the metrics read them), neither run left the
unit in English (reverted segments, failed blocks), and the unit contains a term that v2 **changed,
added or removed** (`--dict-a` against `--dict-b`: a term only in one, or with another `keep`,
`policy` or Russian rendering) **whose rendering differs** between the two runs: the rendering of a
term in a unit is the set of its candidates found in the Russian (the term in Latin script, its
Russian forms in either dictionary, its auto-glossary suggestions in either run's `glossary.md`;
`course_consistency.unit_label`), and a unit where the term's label is the same in both runs (both
`other` included) is not eligible. Up to `--max` eligible units are drawn at random with `--seed`;
each item shows the English and the two Russian versions as A and B in an order drawn with
`--order-seed`. `count` prints only the number of eligible units (the noise baseline: two runs of
the same dictionary). Writes work/T7/E1-pairs.md (for the owner, in Russian) and work/T7/E1-pairs-key.json
(which side is which run). `score` reads the owner's answers ("A", "B" or "=") and prints wins and
losses of run B (the candidate) against run A.
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "work" / "T7" / "E1-pairs.md"
KEY = ROOT / "work" / "T7" / "E1-pairs-key.json"


def units(run: Path, lang: str = "ru") -> tuple[dict[str, str], dict[str, set[str]]]:
    """English key -> Russian, and the run's glossary: term (casefolded) -> its Russian forms."""
    from talk_autopsy import metrics
    from talk_autopsy.glossary import load_glossary
    from talk_autopsy.segments import load_transcript
    from talk_autopsy.web.notices import left_ids

    translated = load_transcript(run / f"{lang}.json")
    left = left_ids(translated, "translation")
    out: dict[str, str] = {}
    for s in translated.segments:
        if s.id in left:
            continue
        english = " ".join(w.text for w in s.words or [])
        key = " ".join(metrics.tokenize(english))
        if key:
            out.setdefault(key, s.text)
    return out, forms_of(load_glossary(run / "glossary.md"), lang)


def forms_of(glossary, lang: str = "ru") -> dict[str, set[str]]:
    from talk_autopsy.web.course_glossary import suggestion

    out: dict[str, set[str]] = {}
    for e in glossary.entries:
        found = out.setdefault(e.term.casefold(), set())
        for text in (e.translations.get(lang, ""), suggestion(e, lang)):
            if text.strip():
                found.add(text.strip())
    return out


def changed_terms(dict_a: Path, dict_b: Path, lang: str = "ru") -> dict[str, str]:
    """Terms v2 changed, added or removed (casefolded -> spelling): in one only, or other keep/policy/form."""
    from talk_autopsy.glossary import load_glossary

    a = {e.term.casefold(): e for e in load_glossary(dict_a).entries}
    b = {e.term.casefold(): e for e in load_glossary(dict_b).entries}
    out = {}
    for key in set(a) | set(b):
        x, y = a.get(key), b.get(key)
        if x is None or y is None or (x.keep, x.effective_policy(), x.translations.get(lang, "")) != \
                (y.keep, y.effective_policy(), y.translations.get(lang, "")):
            out[key] = (y or x).term
    return out


def candidates(run_a: Path, run_b: Path, dict_a: Path, dict_b: Path) -> list[tuple[str, str, str]]:
    from talk_autopsy.course_consistency import unit_label
    from talk_autopsy.glossary import load_glossary
    from talk_autopsy.web.course_glossary import count

    a, forms_a = units(run_a)
    b, forms_b = units(run_b)
    changed = changed_terms(dict_a, dict_b)
    dict_forms = [forms_of(load_glossary(dict_a)), forms_of(load_glossary(dict_b))]
    found = []
    for key in sorted(set(a) & set(b)):
        for folded, term in sorted(changed.items()):
            if not count(term, key):
                continue
            forms = set().union(*(f.get(folded, set()) for f in (forms_a, forms_b, *dict_forms)))
            if unit_label(term, a[key], forms) != unit_label(term, b[key], forms):
                found.append((key, a[key], b[key]))
                break
    return found


def count_only(args) -> None:
    found = candidates(args.run_a, args.run_b, args.dict_a, args.dict_b)
    print(f"eligible units ({args.run_a.name} vs {args.run_b.name}): {len(found)}")


def sample(args) -> None:
    found = candidates(args.run_a, args.run_b, args.dict_a, args.dict_b)
    drawn = random.Random(args.seed).sample(found, min(args.max, len(found)))
    order = random.Random(args.order_seed)
    key, lines = [], [
        "# Сравнение переводов: какой лучше?", "",
        "**Что сделать.** Ниже фразы из видео на английском и два русских перевода, A и B. Порядок "
        "случайный: какой из них сделан с каким словарём, вы не знаете. В строке «Ответ» напишите **A**, "
        "если лучше A; **B**, если лучше B; **=**, если одинаково (или оба одинаково плохи). Смотрите в "
        "первую очередь на термины и на то, как читается фраза. Сохраните файл и напишите в чат «готово».",
        "", f"Фраз: {len(drawn)} (из {len(found)}, где термин словаря переведён по-разному).", ""]
    for n, (english, ru_a, ru_b) in enumerate(drawn, 1):
        b_first = order.random() < 0.5
        first, second = (ru_b, ru_a) if b_first else (ru_a, ru_b)
        key.append({"item": n, "A": "B" if b_first else "A", "B": "A" if b_first else "B"})
        lines += [f"## {n}", "", f"EN: {english}", "", f"A: {first}", "", f"B: {second}", "", "Ответ: ", ""]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    KEY.write_text(json.dumps({"run_a": str(args.run_a), "run_b": str(args.run_b), "seed": args.seed,
                               "order_seed": args.order_seed, "differing": len(found), "items": key},
                              indent=1), encoding="utf-8")
    print(f"{len(found)} differing units with a term; {len(drawn)} drawn -> {OUT}")


def score(_args) -> None:
    key = json.loads(KEY.read_text(encoding="utf-8"))
    answers = re.findall(r"^## (\d+)\s.*?^Ответ:[ \t]*(\S*)", OUT.read_text(encoding="utf-8"), re.M | re.S)
    by_item = {int(n): a.strip().upper().replace("А", "A").replace("В", "B") for n, a in answers}
    wins = losses = ties = missing = 0
    for item in key["items"]:
        answer = by_item.get(item["item"], "")
        if answer in ("A", "B"):
            run = item[answer]  # which run the chosen side shows
            wins += run == "B"
            losses += run == "A"
        elif answer == "=":
            ties += 1
        else:
            missing += 1
    print(f"run B (candidate) against run A: wins {wins}, losses {losses}, same {ties}, "
          f"unanswered {missing}; wins - losses = {wins - losses}; differing units {key['differing']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("sample", "count"):
        s = sub.add_parser(name)
        s.add_argument("run_a", type=Path)
        s.add_argument("run_b", type=Path)
        s.add_argument("--dict-a", type=Path, required=True)
        s.add_argument("--dict-b", type=Path, required=True)
        s.add_argument("--seed", type=int, default=20261116)
        s.add_argument("--order-seed", type=int, default=20261117)
        s.add_argument("--max", type=int, default=20)
    sub.add_parser("score")
    args = parser.parse_args()
    {"sample": sample, "count": count_only, "score": score}[args.command](args)


if __name__ == "__main__":
    main()
