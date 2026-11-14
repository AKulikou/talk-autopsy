"""
C2b measurement (docs/experiments.md, "C2b course glossary"): how many terms a course's lessons
render in Russian differently from one lesson to another. Read only from the lessons' saved run
files; no model call.

For one condition (lessons 1..n of one course, each a pipeline work folder):

1. **Terms**: every term of the run glossaries (`glossary.md`: the user glossary merged with the
   auto-glossary) of all lessons of **both** conditions, compared case-insensitively, so the two
   conditions are scored on the same list.
2. **Occurrence**: a term occurs in a lesson when it is in the English of at least one translated
   sentence unit (`<lang>.json`: a unit's English is its source words; whole words,
   case-insensitive, `course_glossary.count`).
3. **Candidates** of a term: the term itself in Latin script ("keep"), every agreed rendering of it
   (the `ru` column) and every auto-glossary suggestion (`suggested ru: ...` in its notes) found in
   any lesson's run glossary of either condition.
4. **Label of a unit** (with the term in its English): the sorted set of candidates present in its
   Russian: the Latin term by whole-word match, a Russian candidate by the translation check's own
   inflection rule (`translate._stem_present`); `other` when none is present.
5. **Rendering of a term in a lesson**: the label most units have (ties: the first in sorted order).
6. **Inconsistent term**: it occurs in at least two lessons and its renderings differ between them.

Reported next to it: the units left in English or uncorrected by the checks (`reverted_segments`
and failed blocks of the translation and the correction), and the terms whose rendering is
`other` in some lesson (a rendering outside the candidates cannot be told apart, so two `other`
renderings count as the same: a known limit of the automatic measure).
"""

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from talk_autopsy.glossary import load_glossary
from talk_autopsy.segments import load_transcript
from talk_autopsy.translate import _stem_present
from talk_autopsy.web.course_glossary import count, suggestion
from talk_autopsy.web.notices import left_ids

OTHER = "other"


@dataclass
class Lesson:
    name: str
    units: list[tuple[str, str]]  # (English, Russian)
    glossary_terms: dict[str, set[str]]  # term (casefolded) -> Russian candidates from this run's glossary
    terms: dict[str, str]  # casefolded -> the spelling first seen
    left_english: int = 0  # translation: reverted segments + segments of failed blocks
    uncorrected: int = 0  # correction: the same


def load_lesson(work: Path, lang: str = "ru") -> Lesson:
    work = Path(work)
    translated = load_transcript(work / f"{lang}.json")
    units = [(" ".join(w.text for w in s.words or []), s.text) for s in translated.segments]
    glossary = load_glossary(work / "glossary.md")
    candidates: dict[str, set[str]] = {}
    terms: dict[str, str] = {}
    for entry in glossary.entries:
        key = entry.term.casefold()
        terms.setdefault(key, entry.term)
        found = candidates.setdefault(key, set())
        for text in (entry.translations.get(lang, ""), suggestion(entry, lang)):
            if text.strip():
                found.add(text.strip())
    fixed = load_transcript(work / "fixed.json")
    return Lesson(work.name, units, candidates, terms, len(left_ids(translated, "translation")),
                  len(left_ids(fixed, "correction")))


@dataclass
class Result:
    terms: int = 0  # terms scored (the shared list)
    multi: int = 0  # terms occurring in at least two lessons
    inconsistent: list[dict] = field(default_factory=list)
    with_other: int = 0
    left_english: list[int] = field(default_factory=list)
    uncorrected: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"terms": self.terms, "in_two_or_more_lessons": self.multi,
                "inconsistent": len(self.inconsistent), "with_other": self.with_other,
                "left_english_per_lesson": self.left_english, "uncorrected_per_lesson": self.uncorrected,
                "inconsistent_terms": self.inconsistent}


def unit_label(term: str, russian: str, candidates: set[str]) -> str:
    present = [f"keep:{term}"] if count(term, russian) else []
    present += [c for c in sorted(candidates) if _stem_present(c, russian)]
    return " + ".join(sorted(present)) or OTHER


def rendering(term: str, lesson: Lesson, candidates: set[str]) -> str | None:
    """The label most of the lesson's units with the term have; None if the term does not occur."""
    labels = Counter(unit_label(term, ru, candidates) for en, ru in lesson.units if count(term, en))
    if not labels:
        return None
    best = max(labels.values())
    return sorted(label for label, n in labels.items() if n == best)[0]


def measure(lessons: list[Lesson], all_lessons: list[Lesson]) -> Result:
    """`lessons`: one condition, in course order; `all_lessons`: every lesson of both conditions."""
    terms: dict[str, str] = {}
    candidates: dict[str, set[str]] = {}
    for lesson in all_lessons:
        for key, spelling in lesson.terms.items():
            terms.setdefault(key, spelling)
            candidates.setdefault(key, set()).update(lesson.glossary_terms.get(key, set()))
    result = Result(terms=len(terms), left_english=[x.left_english for x in lessons],
                    uncorrected=[x.uncorrected for x in lessons])
    for key in sorted(terms):
        term = terms[key]
        found = {lesson.name: r for lesson in lessons
                 if (r := rendering(term, lesson, candidates[key])) is not None}
        if len(found) < 2:
            continue
        result.multi += 1
        if OTHER in found.values():
            result.with_other += 1
        if len(set(found.values())) > 1:
            result.inconsistent.append({"term": term, "renderings": found})
    return result


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="C2b: terms rendered differently between lessons.")
    parser.add_argument("--without", nargs="+", type=Path, required=True,
                        help="lesson work folders, in order")
    parser.add_argument("--with", dest="with_", nargs="+", type=Path, required=True)
    parser.add_argument("--lang", default="ru")
    parser.add_argument("--json", type=Path, help="write the full result here (local: holds terms)")
    args = parser.parse_args(argv)
    without = [load_lesson(p, args.lang) for p in args.without]
    with_ = [load_lesson(p, args.lang) for p in args.with_]
    everything = without + with_
    out = {"without": measure(without, everything).as_dict(), "with": measure(with_, everything).as_dict()}
    for name, r in out.items():
        print(f"{name:8} terms {r['terms']}, in 2+ lessons {r['in_two_or_more_lessons']}, inconsistent "
              f"{r['inconsistent']} (with 'other': {r['with_other']}); left in English per lesson "
              f"{r['left_english_per_lesson']}, uncorrected {r['uncorrected_per_lesson']}")
    if args.json:
        args.json.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
