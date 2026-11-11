"""
Pre-registered translation check on a hold-out fragment (docs/experiments.md, T6
protocol; written 2026-11-11, before the T6 run). Evaluation tooling, not part of
the frozen pipeline.

    fixed.json + <lang>.json of the run
      -> sample: SAMPLE_SIZE sentence units drawn at random with SEED -> sample.json
      -> baseline: the same English units translated by Gemini with a plain prompt -> baseline.json
      -> rating file: 2 x SAMPLE_SIZE items (pipeline and baseline) shuffled together, one at a
         time, English above each, no system named, a unit's two items never adjacent
         -> C1-T6-rating.md + C1-T6-key.json
      -> score (after the owner rates): un-blind, means, the paired difference with a bootstrap
         interval, and the rule: C1 on the fragment is met if the pipeline's mean >= C1_TARGET.

The English of a unit is rebuilt from the run's `fixed.json` with `units.make_units`
(the units the translation step translated); its ids must equal the translated
file's ids, so the English and the translation belong together. Nothing is printed
here: the sealed fragment's text goes only into the files the caller names.
"""

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

from talk_autopsy import units as units_module
from talk_autopsy.segments import Transcript

SEED = 20261111  # recorded in docs/experiments.md before the run; the shuffle uses SEED + 1
SAMPLE_SIZE = 20
C1_TARGET = 4.0
BOOTSTRAP_RESAMPLES = 10000
PIPELINE, BASELINE = "pipeline", "baseline"
VERSION = "translation-check-v1"

LANGUAGE_NAMES = {"ru": "Russian"}  # the baseline prompt names the language; only Russian is measured

BASELINE_PROMPT = (
    "Translate this English sentence from a technical talk into natural {language} for subtitles. "
    "Answer with the translation only.\n\n{english}"
)
BASELINE_SCHEMA = {
    "type": "object",
    "properties": {"translation": {"type": "string"}},
    "required": ["translation"],
}


class CheckError(ValueError):
    """The inputs of the translation check do not fit together, or a rating is incomplete."""


@dataclass(frozen=True)
class Item:
    unit: int  # sentence-unit id
    english: str
    translation: str


def paired_units(fixed: Transcript, translated: Transcript) -> list[Item]:
    """English units rebuilt from `fixed` next to the translated units, by id; other ids are an error."""
    english = units_module.make_units(fixed)
    if english.ids() != translated.ids():
        raise CheckError("the translated file's units are not the units of this fixed.json "
                         f"({len(translated.ids())} translated, {len(english.ids())} rebuilt)")
    return [Item(e.id, e.text.strip(), t.text.strip())
            for e, t in zip(english.segments, translated.segments, strict=True)]


def sample(items: list[Item], size: int = SAMPLE_SIZE, seed: int = SEED) -> list[Item]:
    """`size` units with English text, drawn with `random.Random(seed)`, in id order."""
    candidates = [i for i in items if i.english and i.translation]
    if len(candidates) < size:
        raise CheckError(f"only {len(candidates)} units with text; {size} are needed")
    chosen = random.Random(seed).sample(range(len(candidates)), size)
    return [candidates[k] for k in sorted(chosen)]


def baseline_translations(items: list[Item], caller,
                          target_lang: str = "ru") -> tuple[dict[int, str], float]:
    """
    One plain request per unit to `caller` (judge.GeminiCaller or a fake with
    `generate(contents, schema)` and `spent_usd`). Returns unit id -> translation and the spend.
    """
    language = LANGUAGE_NAMES.get(target_lang)
    if language is None:
        raise CheckError(f"no language name for {target_lang!r}; add it to LANGUAGE_NAMES")
    out = {}
    for item in items:
        response = caller.generate([BASELINE_PROMPT.format(language=language, english=item.english)],
                                   BASELINE_SCHEMA)
        try:
            text = json.loads(response.text or "")["translation"].strip()
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            raise CheckError(f"unit {item.unit}: the baseline answer is malformed") from None
        if not text:
            raise CheckError(f"unit {item.unit}: the baseline translation is empty")
        out[item.unit] = text
    return out, round(float(getattr(caller, "spent_usd", 0.0)), 6)


MAX_SHUFFLES = 1000

RATING_INSTRUCTIONS = [  # for the owner, who rates in Russian (prompt 15)
    "# Оценка перевода",
    "",
    "Оцените каждый пункт отдельно, от 1 до 5: 5 — субтитр, который вы приняли бы как есть;",
    "1 — неверно или непригодно. Пункты от двух разных систем идут в случайном порядке; один и тот же",
    "английский текст может встретиться дважды. Оценку поставьте в строке Score, комментарий — по желанию.",
    "",
]


def _pairs_adjacent(entries: list) -> bool:
    return any(a[0].unit == b[0].unit for a, b in zip(entries, entries[1:], strict=False))


def rating_file(items: list[Item], baseline: dict[int, str], seed: int = SEED + 1) -> tuple[str, dict]:
    """
    The owner's rating file and its key: every unit twice (pipeline, baseline),
    shuffled together with `random.Random(seed)`, numbered 1..2n, the English
    above each item, no system named. The two items of one unit are never
    adjacent: the same generator shuffles the original order again until none
    are (amendment of
    2026-11-11, written before the T6 run; the order never depends on the
    text). Returns (Markdown, key).
    """
    missing = [i.unit for i in items if i.unit not in baseline]
    if missing:
        raise CheckError(f"no baseline translation for units {missing}")
    original = ([(i, PIPELINE, i.translation) for i in items]
                + [(i, BASELINE, baseline[i.unit]) for i in items])
    rng, shuffles, entries = random.Random(seed), 0, None
    while entries is None or _pairs_adjacent(entries):
        if shuffles == MAX_SHUFFLES:
            raise CheckError(f"no order without adjacent pairs in {MAX_SHUFFLES} shuffles")
        entries = list(original)  # each attempt shuffles the original order with the same generator
        rng.shuffle(entries)
        shuffles += 1
    lines = list(RATING_INSTRUCTIONS)
    key = {"version": VERSION, "seed": seed, "shuffles": shuffles, "items": {}}
    for number, (item, system, text) in enumerate(entries, start=1):
        lines += [f"## {number}", "", f"EN: {item.english}", "", f"{text}", "", "Score (1-5):", "",
                  "Comment:", ""]
        key["items"][str(number)] = {"unit": item.unit, "system": system}
    return "\n".join(lines), key


_ITEM = re.compile(r"^## (\d+)\s*$")
_SCORE = re.compile(r"^Score \(1-5\):\s*(\d)?\s*$")


def parse_scores(text: str) -> dict[int, int]:
    """Item number -> score; an empty or out-of-range score is an error."""
    scores, current = {}, None
    for line in text.splitlines():
        if match := _ITEM.match(line.strip()):
            current = int(match.group(1))
        elif current is not None and (match := _SCORE.match(line.strip())):
            if match.group(1) is None or not 1 <= int(match.group(1)) <= 5:
                raise CheckError(f"item {current}: score missing or not 1-5")
            scores[current] = int(match.group(1))
    return scores


@dataclass(frozen=True)
class CheckResult:
    pipeline_mean: float
    baseline_mean: float
    difference: float  # pipeline - baseline
    interval: tuple[float, float]  # bootstrap 95% of the paired difference
    units: int
    met: bool  # the rule: pipeline mean >= C1_TARGET


def score(scores: dict[int, int], key: dict, seed: int = SEED,
          resamples: int = BOOTSTRAP_RESAMPLES) -> CheckResult:
    """
    Un-blind the owner's scores. The rule (written before the run): C1 on the
    fragment is met if the pipeline's mean >= C1_TARGET. The difference to the
    baseline is reported with a bootstrap 95% interval (units resampled with
    replacement, paired); no other conclusion is drawn.
    """
    items = key["items"]
    if set(scores) != {int(n) for n in items}:
        raise CheckError(f"{len(scores)} scores for {len(items)} items")
    by_unit: dict[int, dict[str, int]] = {}
    for number, meta in items.items():
        by_unit.setdefault(meta["unit"], {})[meta["system"]] = scores[int(number)]
    pairs = [(u[PIPELINE], u[BASELINE]) for u in by_unit.values()]
    if any(len(u) != 2 for u in by_unit.values()):
        raise CheckError("every unit needs one pipeline and one baseline score")
    n = len(pairs)
    pipeline_mean = sum(p for p, _ in pairs) / n
    baseline_mean = sum(b for _, b in pairs) / n
    rng = random.Random(seed)
    diffs = sorted(sum(p - b for p, b in (pairs[rng.randrange(n)] for _ in range(n))) / n
                   for _ in range(resamples))
    interval = (diffs[int(0.025 * resamples)], diffs[int(0.975 * resamples) - 1])
    return CheckResult(pipeline_mean, baseline_mean, pipeline_mean - baseline_mean, interval, n,
                       pipeline_mean >= C1_TARGET)


def write_json(path: Path, data) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
