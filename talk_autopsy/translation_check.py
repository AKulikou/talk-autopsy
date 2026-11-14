"""
Pre-registered translation check on a hold-out fragment (docs/experiments.md, T6
protocol; written 2026-11-11, before the T6 run). Evaluation tooling, not part of
the frozen pipeline.

    fixed.json + <lang>.json of the run
      -> sample: SAMPLE_SIZE sentence units with at least MIN_WORDS English words, drawn at random
         with SEED -> sample.json
      -> baseline: the same English units translated by Gemini with a plain prompt -> baseline.json
      -> rating file: at most 2 x SAMPLE_SIZE items (pipeline and baseline) shuffled together, one at
         a time, English above each, no system named, a unit's two items never adjacent; a unit
         whose two translations are identical after whitespace and punctuation normalisation is
         shown once and its score counts for both -> C1-T6-rating.md + C1-T6-key.json
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
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from talk_autopsy import metrics
from talk_autopsy import units as units_module
from talk_autopsy.segments import Transcript

SEED = 20261111  # recorded in docs/experiments.md before the run; the shuffle uses SEED + 1
SAMPLE_SIZE = 12  # owner's amendment before the T6 run (2026-11-11 20:00); was 20
MIN_WORDS = 6  # English words of a sampled unit, counted by metrics.tokenize (same amendment)
C1_TARGET = 4.0
BOOTSTRAP_RESAMPLES = 10000
PIPELINE, BASELINE, SHARED = "pipeline", "baseline", "shared"
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
    """
    `size` units with a translation and at least MIN_WORDS English words
    (`metrics.tokenize`, the metrics' one normalization), drawn with
    `random.Random(seed)` from those units in id order; returned in id order.
    """
    candidates = [i for i in items if i.translation and len(metrics.tokenize(i.english)) >= MIN_WORDS]
    if len(candidates) < size:
        raise CheckError(f"only {len(candidates)} units with {MIN_WORDS}+ words; {size} are needed")
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

RATING_INSTRUCTIONS = [  # for the owner, who rates in Russian
    "# Оценка перевода",
    "",
    "Оцените каждый пункт отдельно, от 1 до 5: 5 — субтитр, который вы приняли бы как есть;",
    "1 — неверно или непригодно. Пункты от двух разных систем идут в случайном порядке; один и тот же",
    "английский текст может встретиться дважды. Оценку поставьте в строке Score, комментарий — по желанию.",
    "",
]


def _pairs_adjacent(entries: list) -> bool:
    return any(a[0].unit == b[0].unit for a, b in zip(entries, entries[1:], strict=False))


def normalized(text: str) -> str:
    """Punctuation (Unicode category P*) removed, whitespace collapsed; case kept."""
    without = "".join(" " if unicodedata.category(c).startswith("P") else c for c in text)
    return " ".join(without.split())


def rating_file(items: list[Item], baseline: dict[int, str], seed: int = SEED + 1) -> tuple[str, dict]:
    """
    The owner's rating file and its key: every unit twice (pipeline, baseline),
    shuffled together with `random.Random(seed)`, numbered 1..n, the English
    above each item, no system named. A unit whose two translations are equal
    after `normalized` is shown once (the pipeline's text) as a shared item whose
    score counts for both systems (owner's amendment before the T6 run). The two
    items of one unit are never adjacent: the same generator shuffles the
    original order again until none are (amendment of 2026-11-11, written before
    the T6 run). Returns (Markdown, key).
    """
    missing = [i.unit for i in items if i.unit not in baseline]
    if missing:
        raise CheckError(f"no baseline translation for units {missing}")
    shared = [i.unit for i in items if normalized(i.translation) == normalized(baseline[i.unit])]
    original = ([(i, SHARED if i.unit in shared else PIPELINE, i.translation) for i in items]
                + [(i, BASELINE, baseline[i.unit]) for i in items if i.unit not in shared])
    rng, shuffles, entries = random.Random(seed), 0, None
    while entries is None or _pairs_adjacent(entries):
        if shuffles == MAX_SHUFFLES:
            raise CheckError(f"no order without adjacent pairs in {MAX_SHUFFLES} shuffles")
        entries = list(original)  # each attempt shuffles the original order with the same generator
        rng.shuffle(entries)
        shuffles += 1
    lines = list(RATING_INSTRUCTIONS)
    key = {"version": VERSION, "seed": seed, "shuffles": shuffles, "shared_units": shared, "items": {}}
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
    replacement, paired); no other conclusion is drawn. A shared item's score
    counts for both systems.
    """
    items = key["items"]
    if set(scores) != {int(n) for n in items}:
        raise CheckError(f"{len(scores)} scores for {len(items)} items")
    by_unit: dict[int, dict[str, int]] = {}
    for number, meta in items.items():
        systems = (PIPELINE, BASELINE) if meta["system"] == SHARED else (meta["system"],)
        for system in systems:
            if system in by_unit.setdefault(meta["unit"], {}):
                raise CheckError(f"unit {meta['unit']}: two {system} scores")
            by_unit[meta["unit"]][system] = scores[int(number)]
    if any(len(u) != 2 for u in by_unit.values()):
        raise CheckError("every unit needs one pipeline and one baseline score")
    pairs = [(u[PIPELINE], u[BASELINE]) for u in by_unit.values()]
    n = len(pairs)
    pipeline_mean = sum(p for p, _ in pairs) / n
    baseline_mean = sum(b for _, b in pairs) / n
    rng = random.Random(seed)
    diffs = sorted(sum(p - b for p, b in (pairs[rng.randrange(n)] for _ in range(n))) / n
                   for _ in range(resamples))
    interval = (diffs[int(0.025 * resamples)], diffs[int(0.975 * resamples) - 1])
    return CheckResult(pipeline_mean, baseline_mean, pipeline_mean - baseline_mean, interval, n,
                       pipeline_mean >= C1_TARGET)


def load_sample(path: Path) -> list[Item]:
    """The sampled items of `sample.json` (written by the `sample` step)."""
    return [Item(**i) for i in json.loads(Path(path).read_text(encoding="utf-8"))["items"]]


def write_json(path: Path, data) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# --- T7 (v1.2): one variant per replica, with the term question ----------------------------------
# docs/experiments.md, "T7 baseline (v1.2)", written before any T7 baseline run. Owner's change of
# 2026-11-14 (before the pre-registration was committed): no second system; only the pipeline's
# translation of 20 replicas, a score and "scored lower because of a term" with the term.

SINGLE_VERSION = "translation-check-single-v1"
T7_SEED = 20261114  # the sample uses it, the order T7_SEED + 1
T7_SAMPLE_SIZE = 20
E1_TERM_ACCURACY = 0.90  # E1 launch rule (specification), mean of the three (b) runs, reference J
E1_BLIND_MEAN = 4.0
E1_TERM_LOWERED = 2  # E1 launches if MORE than this many replicas were scored lower because of a term

SINGLE_INSTRUCTIONS = [  # for the owner, who rates in Russian
    "# Оценка перевода T7",
    "",
    "Оцените каждый из 20 субтитров отдельно, от 1 до 5: 5 — субтитр, который вы приняли бы как есть;",
    "1 — неверно или непригодно. Оценку поставьте в строке Score. Если оценка снижена из-за термина",
    "(название, команда, идентификатор, номер версии, термин Web3 переведён или записан неверно), поставьте",
    "x в скобках строки Term — [x] — и напишите этот термин в строке Which term. Комментарий — по желанию.",
    "Английский текст стоит над переводом для справки. Сохраните файл и сообщите в той же сессии.",
    "",
]
_TERM = re.compile(r"^Term:\s*\[(.?)\]\s*$")
_WHICH = re.compile(r"^Which term:\s*(.*)$")


def rating_file_single(items: list[Item], seed: int = T7_SEED + 1) -> tuple[str, dict]:
    """
    One item per sampled unit (the pipeline's translation only), in an order shuffled with
    `random.Random(seed)`, numbered 1..n, the English above each item. Returns (Markdown, key).
    """
    entries = list(items)
    random.Random(seed).shuffle(entries)
    lines = list(SINGLE_INSTRUCTIONS)
    key = {"version": SINGLE_VERSION, "seed": seed, "items": {}}
    for number, item in enumerate(entries, start=1):
        lines += [f"## {number}", "", f"EN: {item.english}", "", f"{item.translation}", "",
                  "Score (1-5):", "", "Term: [ ]", "", "Which term:", "", "Comment:", ""]
        key["items"][str(number)] = {"unit": item.unit}
    return "\n".join(lines), key


@dataclass(frozen=True)
class SingleScore:
    score: int
    term_lowered: bool
    term: str


def parse_single(text: str) -> dict[int, SingleScore]:
    """Item number -> score, term flag and term; a missing score or a flag without a term is an error."""
    found: dict[int, dict] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if match := _ITEM.match(line):
            current = int(match.group(1))
            found[current] = {"score": None, "flag": False, "term": ""}
        elif current is None:
            continue
        elif match := _SCORE.match(line):
            if match.group(1) is None or not 1 <= int(match.group(1)) <= 5:
                raise CheckError(f"item {current}: score missing or not 1-5")
            found[current]["score"] = int(match.group(1))
        elif match := _TERM.match(line):
            found[current]["flag"] = match.group(1).strip().lower() in ("x", "х")  # Latin or Cyrillic x
        elif match := _WHICH.match(line):
            found[current]["term"] = match.group(1).strip()
    out = {}
    for number, f in found.items():
        if f["score"] is None:
            raise CheckError(f"item {number}: score missing or not 1-5")
        if f["flag"] and not f["term"]:
            raise CheckError(f"item {number}: marked as lower because of a term, but no term written")
        out[number] = SingleScore(f["score"], f["flag"], f["term"] if f["flag"] else "")
    return out


@dataclass(frozen=True)
class SingleResult:
    items: int
    mean: float
    distribution: dict[int, int]  # score -> count
    term_lowered: int
    terms: list[str]


def score_single(scores: dict[int, SingleScore], key: dict) -> SingleResult:
    if set(scores) != {int(n) for n in key["items"]}:
        raise CheckError(f"{len(scores)} scored items for {len(key['items'])} items")
    values = [s.score for s in scores.values()]
    flagged = [scores[n] for n in sorted(scores) if scores[n].term_lowered]
    return SingleResult(len(values), sum(values) / len(values), {v: values.count(v) for v in range(5, 0, -1)},
                        len(flagged), [s.term for s in flagged])


def e1_launch(term_accuracy_mean: float, blind_mean: float, term_lowered: int) -> tuple[bool, list[str]]:
    """
    The E1 rule, fixed before the T7 baseline: dictionary v2 is built only if at least one holds:
    term accuracy (mean of the three (b) runs, reference J) < 90%; blind mean < 4.0; more than 2 of
    the 20 replicas scored lower because of a term. Returns (launch, the conditions that hold).
    """
    reasons = []
    if term_accuracy_mean < E1_TERM_ACCURACY:
        reasons.append(f"term accuracy {term_accuracy_mean:.2%} < {E1_TERM_ACCURACY:.0%}")
    if blind_mean < E1_BLIND_MEAN:
        reasons.append(f"blind mean {blind_mean:.2f} < {E1_BLIND_MEAN}")
    if term_lowered > E1_TERM_LOWERED:
        reasons.append(f"{term_lowered} replicas scored lower because of a term > {E1_TERM_LOWERED}")
    return bool(reasons), reasons
