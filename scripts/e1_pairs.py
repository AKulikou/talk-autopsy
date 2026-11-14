"""
E1 decision (docs/experiments.md, E1): a blind pairwise comparison of the translations of two runs.

    uv run python scripts/e1_pairs.py sample <run A> <run B> [--seed 20261116 --order-seed 20261117 --max 20]
    uv run python scripts/e1_pairs.py score

`sample` takes the sentence units (`ru.json`) of the two runs whose English is the same (tokens as
the metrics read them), that contain a term of either run's glossary (`glossary.md`; whole words,
case-insensitive) and whose Russian differs (whitespace ignored), leaving out units either run
left in English (reverted segments, failed blocks). Up to `--max` of them are drawn at random with
`--seed`; each item shows the English and the two Russian versions as A and B in an order drawn with
`--order-seed`. Writes work/T7/E1-pairs.md (for the owner, in Russian) and work/T7/E1-pairs-key.json
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


def units(run: Path, lang: str = "ru") -> tuple[dict[str, str], set[str]]:
    """English key -> Russian, and the run's glossary terms."""
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
    return out, {e.term for e in load_glossary(run / "glossary.md").entries}


def candidates(run_a: Path, run_b: Path) -> list[tuple[str, str, str]]:
    from talk_autopsy.web.course_glossary import count

    a, terms_a = units(run_a)
    b, terms_b = units(run_b)
    terms = sorted(terms_a | terms_b)
    found = []
    for key in sorted(set(a) & set(b)):
        if " ".join(a[key].split()) == " ".join(b[key].split()):
            continue
        if any(count(t, key) for t in terms):
            found.append((key, a[key], b[key]))
    return found


def sample(args) -> None:
    found = candidates(args.run_a, args.run_b)
    drawn = random.Random(args.seed).sample(found, min(args.max, len(found)))
    order = random.Random(args.order_seed)
    key, lines = [], [
        "# Сравнение переводов: какой лучше?", "",
        "**Что сделать.** Ниже фразы из видео на английском и два русских перевода, A и B. Порядок "
        "случайный: какой из них сделан с каким словарём, вы не знаете. В строке «Ответ» напишите **A**, "
        "если лучше A; **B**, если лучше B; **=**, если одинаково (или оба одинаково плохи). Смотрите в "
        "первую очередь на термины и на то, как читается фраза. Сохраните файл и напишите в чат «готово».",
        "", f"Фраз: {len(drawn)} (из {len(found)} различающихся).", ""]
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
    s = sub.add_parser("sample")
    s.add_argument("run_a", type=Path)
    s.add_argument("run_b", type=Path)
    s.add_argument("--seed", type=int, default=20261116)
    s.add_argument("--order-seed", type=int, default=20261117)
    s.add_argument("--max", type=int, default=20)
    sub.add_parser("score")
    args = parser.parse_args()
    (sample if args.command == "sample" else score)(args)


if __name__ == "__main__":
    main()
