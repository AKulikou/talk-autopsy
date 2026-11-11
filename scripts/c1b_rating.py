"""
C1b: the owner's blind rating of translation-v2 against translation-v3
(docs/experiments.md, C1b). Reads the filled blind file and its key, un-blinds
the scores and applies the decision rule written before the rating. No API call.

    uv run python scripts/c1b_rating.py [work/C1-v3-blind.md] [work/C1-v3-key.json]

Prints a Markdown table (pair, source, example word, v2 score, v3 score,
winner) and the totals: wins, ties, means, the lowest v3 score. The second
half of the rule (no v3 score of 2 or less caused by lost meaning) needs the
comments: every v3 score <= 2 is listed with its comment.
"""

import json
import re
import sys
from pathlib import Path

PAIR = re.compile(r"^## (\d+)\. ")
SCORE = re.compile(r"^([AB]) \(1-5\):\s*(\d)?\s*$")
COMMENT = re.compile(r"^Comment:\s*(.*)$")


def parse_blind(text: str) -> dict[int, dict]:
    """Pair number -> {"A": score, "B": score, "comment": text}; a missing score is an error."""
    pairs: dict[int, dict] = {}
    current = None
    for line in text.splitlines():
        if match := PAIR.match(line):
            current = int(match.group(1))
            pairs[current] = {"A": None, "B": None, "comment": ""}
        elif current is not None and (match := SCORE.match(line.strip())):
            if match.group(2) is None:
                raise SystemExit(f"pair {current}: score {match.group(1)} is empty")
            pairs[current][match.group(1)] = int(match.group(2))
        elif current is not None and (match := COMMENT.match(line)):
            pairs[current]["comment"] = match.group(1).strip()
    for number, pair in pairs.items():
        if pair["A"] is None or pair["B"] is None:
            raise SystemExit(f"pair {number}: a score line is missing")
    return pairs


def unblind(pairs: dict[int, dict], key: dict) -> list[dict]:
    rows = []
    for number in sorted(pairs):
        meta = key["pairs"][str(number)]
        by_version = {meta[side]: pairs[number][side] for side in ("A", "B")}
        v2, v3 = by_version["translation-v2"], by_version["translation-v3"]
        winner = "v3" if v3 > v2 else "v2" if v2 > v3 else "tie"
        rows.append({"pair": number, "source": meta["source"], "unit": meta["unit"],
                     "example": meta.get("example"), "v2": v2, "v3": v3, "winner": winner,
                     "comment": pairs[number]["comment"]})
    return rows


def main() -> None:
    blind = Path(sys.argv[1] if len(sys.argv) > 1 else "work/C1-v3-blind.md")
    key_path = Path(sys.argv[2] if len(sys.argv) > 2 else "work/C1-v3-key.json")
    key = json.loads(key_path.read_text(encoding="utf-8"))
    rows = unblind(parse_blind(blind.read_text(encoding="utf-8")), key)
    print("| Pair | Source | Example word | v2 | v3 | Winner |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['pair']} | {r['source']} (unit {r['unit']}) | {r['example'] or '-'} | "
              f"{r['v2']} | {r['v3']} | {r['winner']} |")
    n = len(rows)
    wins = {w: sum(1 for r in rows if r["winner"] == w) for w in ("v2", "v3", "tie")}
    mean2, mean3 = sum(r["v2"] for r in rows) / n, sum(r["v3"] for r in rows) / n
    print()
    print(f"pairs {n}; v3 wins {wins['v3']}, v2 wins {wins['v2']}, ties {wins['tie']}; "
          f"mean v2 {mean2:.2f}, v3 {mean3:.2f}; lowest v3 {min(r['v3'] for r in rows)}, "
          f"lowest v2 {min(r['v2'] for r in rows)}")
    low = [r for r in rows if r["v3"] <= 2]
    for r in low:
        print(f"v3 score <= 2: pair {r['pair']}: {r['comment'] or '(no comment)'}")
    adopted = wins["v3"] > wins["v2"] and not low  # a low v3 score without a comment also blocks
    print(f"rule: v3 wins more pairs than v2 and no v3 score <= 2 from lost meaning -> "
          f"{'v3 adopted' if adopted else 'v3 not adopted, v2 stays the default'}")


if __name__ == "__main__":
    main()
