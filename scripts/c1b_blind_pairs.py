"""
C1b blind pairs: translation-v2 against translation-v3 for the owner (docs/experiments.md, C1b).

    uv run python scripts/c1b_blind_pairs.py

16 pairs chosen by code from sentence units whose v2 and v3 texts differ: 10
from real-01, 6 from T2, at least 8 with one of the owner's example terms in
the English (one unit per example term first, round robin), the rest at
random; options A and B in random order per pair. Seeded, so the same files
give the same pairs. Writes work/C1-v3-blind.md (no version labels) and
work/C1-v3-key.json (which option is v3).
"""

import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from c1b_compare import EXAMPLES, english

from talk_autopsy.segments import load_transcript

SEED = 20261110
SOURCES = [  # name, v2, v3, pairs, of which with example terms
    ("real-01", "work/real-01/ru.json", "work/real-01/v3/ru.json", 10, 9),
    ("T2", "work/T2/T2.v2u.ru.json", "work/T2/v3/ru.json", 6, 3),
]
DECISION_RULE = ("v3 is adopted if, in these blind scores, v3 wins more pairs than v2 and v3 has no pair "
                 "with a score of 2 or less caused by lost meaning. Otherwise v2 stays and the comments "
                 "go to the backlog.")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def mmss(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{seconds % 60:04.1f}"


def pick(rng: random.Random, name: str, v2_path: str, v3_path: str, total: int, with_examples: int) -> list:
    v2, v3 = load_transcript(Path(v2_path)), load_transcript(Path(v3_path))
    by_id = {s.id: s for s in v3.segments}
    differing = [(a, by_id[a.id]) for a in v2.segments
                 if a.id in by_id and norm(a.text) != norm(by_id[a.id].text)]
    tagged = {}
    for example, (pattern, _) in EXAMPLES.items():
        units = [pair for pair in differing if re.search(pattern, english(pair[0]), re.I)]
        if units:
            tagged[example] = units
    chosen, used = [], set()
    def left() -> bool:
        return any(p[0].id not in used for units in tagged.values() for p in units)

    while len(chosen) < with_examples and left():
        for example in sorted(tagged):
            free = [p for p in tagged[example] if p[0].id not in used]
            if free and len(chosen) < with_examples:
                pair = rng.choice(free)
                used.add(pair[0].id)
                chosen.append((name, example, pair))
    rest = [p for p in differing if p[0].id not in used]
    for pair in rng.sample(rest, total - len(chosen)):
        chosen.append((name, "", pair))
    return sorted(chosen, key=lambda c: c[2][0].start)


def main() -> None:
    rng = random.Random(SEED)
    items = []
    for source in SOURCES:
        items += pick(rng, *source)
    lines = [
        "# C1b blind: two Russian translations, 16 pairs",
        "",
        "For the owner. **Do not open `C1-v3-key.json` until every score below is filled in**: it says",
        "which prompt version wrote A and which wrote B. A is not always the same version.",
        "",
        "10 pairs come from the [removed] interview (real-01), 6 from T2. They were chosen by code among the",
        "sentences where the two translations differ, at least 8 of them with one of your example words",
        "(computer science, tokens, thinking tokens, frontier, compute, inference, alignment, prompt,",
        "emergent, Dictation). Both translations were made from the same corrected English, shown first.",
        "",
        "Score each translation 1-5 (5 = a subtitle you would accept as is; 1 = wrong or unreadable). If a",
        "score of 2 or less is because meaning was lost, say so in the comment.",
        "",
        f"Decision rule (written before the scores): {DECISION_RULE}",
        "",
    ]
    key = {"seed": SEED, "rule": DECISION_RULE, "pairs": {}}
    for number, (name, example, (v2s, v3s)) in enumerate(items, start=1):
        v3_is_a = rng.random() < 0.5
        a, b = (v3s.text, v2s.text) if v3_is_a else (v2s.text, v3s.text)
        lines += [f"## {number}. [{mmss(v2s.start)}] {name}, unit {v2s.id}", "",
                  f"- en: {english(v2s)}", f"- A: {norm(a)}", f"- B: {norm(b)}", "",
                  "A (1-5): ", "B (1-5): ", "Comment: ", ""]
        key["pairs"][str(number)] = {"source": name, "unit": v2s.id, "example": example or None,
                                     "A": "translation-v3" if v3_is_a else "translation-v2",
                                     "B": "translation-v2" if v3_is_a else "translation-v3"}
    Path("work/C1-v3-blind.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    Path("work/C1-v3-key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1) + "\n",
                                           encoding="utf-8")
    with_example = sum(1 for _, example, _ in items if example)
    print(f"{len(items)} pairs ({with_example} with an example term) -> work/C1-v3-blind.md, "
          "work/C1-v3-key.json")


if __name__ == "__main__":
    main()
