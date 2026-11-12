"""
Translation rating of the baselines on T6 (docs/experiments.md, Baselines on one
set > Translation rating; pre-registered 2026-11-11). Evaluation tooling, not part
of the frozen pipeline. It builds on the T6 translation check (`translation_check`):

    first rating (22 items: pipeline, Gemini, 2 shared; already rated by the owner)
      + Google Translate items of the same 12 units (T6-units.json)
      -> second rating file: 12 Google Translate items + 4 anchors (items of the first
         file, rated again to measure drift) -> C1-T6-rating-2.md + C1-T6-key-2.json
      -> Gemini as a second rater of all 38 items (informational; never used for a decision)
      -> score_baselines (after the owner has rated): means per system over the 12 units,
         paired bootstrap intervals of each difference to the pipeline, drift on the anchors,
         the specification's C1 Google Translate part, the rater's agreement with the owner
      -> the result section of docs/experiments.md and the README column (between markers).

Nothing here prints text of the fragment; it goes only into the files the caller names.
"""

import json
import math
import random
import re
from dataclasses import dataclass, field

from talk_autopsy import translation_check as tc

VERSION = "translation-rating-v1"
ANCHOR_SEED = 20261113  # anchors: Random(ANCHOR_SEED).sample, pipeline first
SHUFFLE_SEED = 20261114  # the second file's order
RATER_SEED = 20261115  # the Gemini rater's order of the 38 items
BOOTSTRAP_SEED = tc.SEED  # the first check's seed, so pipeline - Gemini reproduces its interval
ANCHORS_PER_SYSTEM = 2
DRIFT_LIMIT = 1.0
C1_GT_MARGIN = 1.0  # specification C1: Claude's mean at least 1 point above Google Translate

PIPELINE, GEMINI, GOOGLE = tc.PIPELINE, tc.BASELINE, "google-translate"
SYSTEM_NAMES = {PIPELINE: "Talk Autopsy (`pipeline-v3`)", GEMINI: "Gemini translation (plain prompt)",
                GOOGLE: "Whisper + Google Translate"}

RATING_INSTRUCTIONS = [line.replace("двух разных систем", "разных систем") for line in tc.RATING_INSTRUCTIONS]

RATER_VERSION = "gemini-rater-v1"
RATER_PROMPT = (
    "Оцените перевод субтитра от 1 до 5: 5 — субтитр, который вы приняли бы как есть; "
    "1 — неверно или непригодно. Сверху — английский текст, ниже — перевод.\n\n"
    "EN: {english}\n\n{translation}\n\n"
    "Answer with a short reason in English (at most 25 words) and the score, an integer from 1 to 5."
)
RATER_SCHEMA = {
    "type": "object",
    "properties": {"reason": {"type": "string"}, "score": {"type": "integer", "enum": [1, 2, 3, 4, 5]}},
    "required": ["reason", "score"],
}
RATER_BUDGET_USD = 0.30
RATER_MARGIN_USD = 0.01  # stop before a request once the spend is within this of the budget


class RatingError(ValueError):
    """The inputs of the baseline rating do not fit together, or a rating is incomplete."""


@dataclass(frozen=True)
class Shown:
    """One item of a rating file as the rater saw it."""
    unit: int
    english: str
    translation: str


_ITEM = re.compile(r"^## (\d+)\s*$")


def parse_items(text: str) -> dict[int, Shown]:
    """Item number -> what was shown (the `EN:` line and the translation), from a rating file."""
    out: dict[int, Shown] = {}
    blocks = re.split(r"(?m)^(?=## \d+\s*$)", text)
    for block in blocks:
        lines = block.splitlines()
        if not lines or not (match := _ITEM.match(lines[0].strip())):
            continue
        body = [line for line in lines[1:] if line.strip()]
        if len(body) < 2 or not body[0].startswith("EN: "):
            raise RatingError(f"item {match.group(1)}: no EN line and translation")
        out[int(match.group(1))] = Shown(-1, body[0][len("EN: "):], body[1])
    return out


def with_units(shown: dict[int, Shown], key: dict) -> dict[int, Shown]:
    """The parsed items with their unit ids from the key (the files carry no ids)."""
    if set(shown) != {int(n) for n in key["items"]}:
        raise RatingError(f"{len(shown)} items in the file, {len(key['items'])} in the key")
    return {n: Shown(key["items"][str(n)]["unit"], s.english, s.translation) for n, s in shown.items()}


def choose_anchors(key1: dict, seed: int = ANCHOR_SEED) -> list[int]:
    """
    Item numbers of the first file to show again: ANCHORS_PER_SYSTEM pipeline-only
    items, then as many Gemini-only items of other units, drawn with one
    `random.Random(seed)` from the item numbers in ascending order (shared items excluded).
    """
    rng = random.Random(seed)
    numbers = sorted(int(n) for n in key1["items"])
    pipeline = [n for n in numbers if key1["items"][str(n)]["system"] == PIPELINE]
    chosen = rng.sample(pipeline, ANCHORS_PER_SYSTEM)
    taken = {key1["items"][str(n)]["unit"] for n in chosen}
    gemini = [n for n in numbers
              if key1["items"][str(n)]["system"] == GEMINI and key1["items"][str(n)]["unit"] not in taken]
    return chosen + rng.sample(gemini, ANCHORS_PER_SYSTEM)


def _adjacent(entries: list[tuple]) -> bool:
    return any(a[0] == b[0] for a, b in zip(entries, entries[1:], strict=False))


def rating_file_2(first: dict[int, Shown], key1: dict, google: dict[int, str],
                  seed: int = SHUFFLE_SEED) -> tuple[str, dict]:
    """
    The second rating file and its key: one Google Translate item per unit of the
    first check (the same English line as the first file showed) + the anchors,
    shuffled with `random.Random(seed)`; the original order is reshuffled with the
    same generator until no two items of one unit are adjacent. Returns (Markdown, key).
    """
    english = {s.unit: s.english for s in first.values()}
    if set(google) != set(english):
        raise RatingError(f"Google Translate units {sorted(google)} != the check's units {sorted(english)}")
    anchors = choose_anchors(key1)
    original = [(unit, GOOGLE, english[unit], google[unit], None) for unit in sorted(google)]
    original += [(first[n].unit, key1["items"][str(n)]["system"], first[n].english, first[n].translation, n)
                 for n in anchors]
    rng, shuffles, entries = random.Random(seed), 0, None
    while entries is None or _adjacent(entries):
        if shuffles == tc.MAX_SHUFFLES:
            raise RatingError(f"no order without adjacent units in {tc.MAX_SHUFFLES} shuffles")
        entries = list(original)
        rng.shuffle(entries)
        shuffles += 1
    lines = list(RATING_INSTRUCTIONS)
    key = {"version": VERSION, "seed": seed, "anchor_seed": ANCHOR_SEED, "shuffles": shuffles,
           "anchors": anchors, "items": {}}
    for number, (unit, system, en, text, anchor_of) in enumerate(entries, start=1):
        lines += [f"## {number}", "", f"EN: {en}", "", f"{text}", "", "Score (1-5):", "", "Comment:", ""]
        meta = {"unit": unit, "system": system}
        if anchor_of is not None:
            meta["anchor_of"] = anchor_of
        key["items"][str(number)] = meta
    return "\n".join(lines), key


# --- the Gemini rater --------------------------------------------------------------------------

def rater_items(first: dict[int, Shown], second: dict[int, Shown]) -> dict[str, Shown]:
    """All items of both files, keyed "1-<n>" and "2-<n>"."""
    return {**{f"1-{n}": s for n, s in first.items()}, **{f"2-{n}": s for n, s in second.items()}}


def rater_order(ids: list[str], seed: int = RATER_SEED) -> list[str]:
    order = sorted(ids, key=lambda i: (int(i.split("-")[0]), int(i.split("-")[1])))
    random.Random(seed).shuffle(order)
    return order


def rate(items: dict[str, Shown], caller, record: dict, save, budget_usd: float = RATER_BUDGET_USD) -> dict:
    """
    One request per item in `record["order"]` that `record["items"]` does not hold yet
    (resumable); `save(record)` after each answer. Stops before a request once the
    spend (`record["cost_usd"]` before this call + the caller's) is within
    RATER_MARGIN_USD of `budget_usd`. Returns the record.
    """
    done = {r["item"] for r in record["items"]}
    spent_before = record["cost_usd"]
    for item_id in record["order"]:
        if item_id in done:
            continue
        if spent_before + caller.spent_usd >= budget_usd - RATER_MARGIN_USD:
            record["stopped"] = f"budget {budget_usd} USD"
            save(record)
            break
        shown = items[item_id]
        prompt = RATER_PROMPT.format(english=shown.english, translation=shown.translation)
        response = caller.generate([prompt], RATER_SCHEMA)
        record["cost_usd"] = round(spent_before + caller.spent_usd, 6)
        try:
            answer = json.loads(response.text or "")
            score, reason = int(answer["score"]), str(answer["reason"]).strip()
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
            raise RatingError(f"{item_id}: the rater's answer is malformed") from None
        if not 1 <= score <= 5:
            raise RatingError(f"{item_id}: score {score} not 1-5")
        record["items"].append({"item": item_id, "score": score, "reason": reason})
        save(record)
    return record


# --- scoring -----------------------------------------------------------------------------------

def paired_bootstrap(pairs: list[tuple[int, int]], seed: int = BOOTSTRAP_SEED,
                     resamples: int = tc.BOOTSTRAP_RESAMPLES) -> tuple[float, float]:
    """95% interval of mean(a - b), units resampled with replacement (translation_check.score's method)."""
    n = len(pairs)
    rng = random.Random(seed)
    diffs = sorted(sum(a - b for a, b in (pairs[rng.randrange(n)] for _ in range(n))) / n
                   for _ in range(resamples))
    return diffs[int(0.025 * resamples)], diffs[int(0.975 * resamples) - 1]


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    """Spearman's rank correlation with average ranks for ties; None if either side is constant."""
    ra, rb = _ranks(a), _ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    va, vb = sum((x - ma) ** 2 for x in ra), sum((y - mb) ** 2 for y in rb)
    return None if va == 0 or vb == 0 else cov / math.sqrt(va * vb)


@dataclass(frozen=True)
class Agreement:
    n: int
    spearman: float | None
    mean_abs_diff: float
    within_one: float
    bias: float  # mean (rater - owner)

    @classmethod
    def of(cls, pairs: list[tuple[int, int]]) -> "Agreement":
        owner, rater = [p[0] for p in pairs], [p[1] for p in pairs]
        n = len(pairs)
        return cls(n, spearman(owner, rater), sum(abs(r - o) for o, r in pairs) / n,
                   sum(abs(r - o) <= 1 for o, r in pairs) / n, sum(r - o for o, r in pairs) / n)


@dataclass(frozen=True)
class BaselineResult:
    units: int
    means: dict[str, float]
    counts: dict[str, dict[int, int]]  # system -> score -> number of units
    differences: dict[str, tuple[float, tuple[float, float]]]  # other system -> (pipeline - other, interval)
    drift_mean_abs: float
    drift_signed: float  # mean (new - old) on the anchors
    anchors: list[tuple[int, int, int]]  # (old item number, old score, new score)
    agreement: dict[str, Agreement] = field(default_factory=dict)  # system or "all" -> rater vs owner
    rater_complete: bool = False

    @property
    def drift_flag(self) -> bool:
        return self.drift_mean_abs >= DRIFT_LIMIT

    @property
    def c1_google_met(self) -> bool:
        return self.differences[GOOGLE][0] >= C1_GT_MARGIN


def _systems(meta: dict) -> tuple[str, ...]:
    return (PIPELINE, GEMINI) if meta["system"] == tc.SHARED else (meta["system"],)


def score_baselines(scores1: dict[int, int], key1: dict, scores2: dict[int, int], key2: dict,
                    rater: dict | None = None) -> BaselineResult:
    """The pre-registered numbers; `rater` (gemini-rater.json) adds the agreement when complete."""
    if set(scores2) != {int(n) for n in key2["items"]}:
        raise RatingError(f"{len(scores2)} scores for {len(key2['items'])} items of the second file")
    tc.score(scores1, key1)  # validates the first rating (one pipeline and one Gemini score per unit)
    by_unit: dict[int, dict[str, int]] = {}
    for number, meta in key1["items"].items():
        for system in _systems(meta):
            by_unit.setdefault(meta["unit"], {})[system] = scores1[int(number)]
    anchors = []
    for number, meta in key2["items"].items():
        if meta["system"] == GOOGLE:
            if GOOGLE in by_unit.get(meta["unit"], {GOOGLE: 0}):
                raise RatingError(f"unit {meta['unit']}: no first-check unit or two Google Translate items")
            by_unit[meta["unit"]][GOOGLE] = scores2[int(number)]
        else:
            anchors.append((meta["anchor_of"], scores1[meta["anchor_of"]], scores2[int(number)]))
    if any(GOOGLE not in u for u in by_unit.values()) or not anchors:
        raise RatingError("every unit needs a Google Translate score, and the file needs anchors")
    units = list(by_unit.values())  # the first key's order, as translation_check.score pairs them
    n = len(units)
    means = {s: sum(u[s] for u in units) / n for s in (PIPELINE, GEMINI, GOOGLE)}
    counts = {s: {k: sum(u[s] == k for u in units) for k in (5, 4, 3, 2, 1)} for s in means}
    differences = {}
    for other in (GEMINI, GOOGLE):
        pairs = [(u[PIPELINE], u[other]) for u in units]
        differences[other] = (means[PIPELINE] - means[other], paired_bootstrap(pairs))
    drift_abs = sum(abs(new - old) for _, old, new in anchors) / len(anchors)
    drift_signed = sum(new - old for _, old, new in anchors) / len(anchors)
    agreement, complete = {}, False
    if rater is not None:
        agreement, complete = rater_agreement(rater, scores1, key1, scores2, key2)
    return BaselineResult(n, means, counts, differences, drift_abs, drift_signed, sorted(anchors),
                          agreement, complete)


def rater_agreement(rater: dict, scores1: dict[int, int], key1: dict, scores2: dict[int, int],
                    key2: dict) -> tuple[dict[str, Agreement], bool]:
    """Owner vs rater per item; per system (a shared item counts for pipeline and Gemini) and over all."""
    given = {r["item"]: r["score"] for r in rater["items"]}
    groups: dict[str, list[tuple[int, int]]] = {PIPELINE: [], GEMINI: [], GOOGLE: [], "all": []}
    expected = 0
    for prefix, scores, key in (("1", scores1, key1), ("2", scores2, key2)):
        for number, meta in key["items"].items():
            expected += 1
            item_id = f"{prefix}-{number}"
            if item_id not in given:
                continue
            pair = (scores[int(number)], given[item_id])
            groups["all"].append(pair)
            for system in _systems(meta):
                groups[system].append(pair)
    agreement = {name: Agreement.of(pairs) for name, pairs in groups.items() if pairs}
    return agreement, len(groups["all"]) == expected


# --- writing the result ------------------------------------------------------------------------

def _f(value: float) -> str:
    return f"{value:+.2f}".replace("-", "−")


def _interval(interval: tuple[float, float]) -> str:
    return f"{_f(interval[0])} … {_f(interval[1])}"


def drift_note(result: BaselineResult) -> str:
    return "rated on a different day; drift ≥ 1 point" if result.drift_flag else ""


def result_markdown(result: BaselineResult, date: str) -> str:
    """The result section of docs/experiments.md (between the markers)."""
    lines = [f"**Result ({date}).** The owner rated `work/T6/C1-T6-rating-2.md` (16 items) blind, one at a "
             "time; scored with `uv run python scripts/translation_check.py score-baselines`.", "",
             f"| {result.units} units, single-rated 1–5 | mean | scores 5 / 4 / 3 / 2 / 1 | "
             "pipeline − system, paired bootstrap 95% |", "|---|---|---|---|"]
    for system in (PIPELINE, GEMINI, GOOGLE):
        counts = " / ".join(str(result.counts[system][k]) for k in (5, 4, 3, 2, 1))
        diff = "" if system == PIPELINE else (
            f"{_f(result.differences[system][0])} ({_interval(result.differences[system][1])})")
        note = f" ({drift_note(result)})" if system == GOOGLE and result.drift_flag else ""
        lines.append(f"| {SYSTEM_NAMES[system]} | **{result.means[system]:.2f}**{note} | {counts} | {diff} |")
    anchors = "; ".join(f"item {n}: {old} -> {new}" for n, old, new in result.anchors)
    lines += ["", f"- **Drift** on the {len(result.anchors)} anchors (first-file item: old -> new score; "
              f"{anchors}): mean absolute difference **{result.drift_mean_abs:.2f}**, signed "
              f"{_f(result.drift_signed)}; "
              + ("**≥ 1.0: the comparison with Google Translate is rated on a different day; "
                 "drift ≥ 1 point.**" if result.drift_flag
                 else "below 1.0, so the two days are comparable by the rule.")]
    gt_diff, gt_interval = result.differences[GOOGLE]
    lines.append(f"- **Specification C1, Google Translate part** (pipeline mean at least "
                 f"{C1_GT_MARGIN:.0f} point above Google Translate): difference {_f(gt_diff)} "
                 f"({_interval(gt_interval)}): "
                 f"**{'met' if result.c1_google_met else 'not met'}** (12 units on T6, not 20 fragments; "
                 "Google Translate rated on another day). The VideoLingo part stays not run.")
    lines.append("- Google Translate translates raw Whisper; the owner saw the corrected English above "
                 "it, so recognition errors count against it. Pipeline and Gemini scores are the "
                 "first rating's.")
    if result.agreement:
        lines += ["", "**Gemini as a second rater** (`gemini-rater-v1`, informational; no rule uses it"
                  + ("" if result.rater_complete else "; **incomplete**: not every item rated") + "):", "",
                  "| items | n | Spearman | mean abs. difference | within ±1 | rater − owner |",
                  "|---|---|---|---|---|---|"]
        for name, label in ((PIPELINE, SYSTEM_NAMES[PIPELINE]), (GEMINI, SYSTEM_NAMES[GEMINI] + " (self)"),
                            (GOOGLE, SYSTEM_NAMES[GOOGLE]), ("all", "all items")):
            if name not in result.agreement:
                continue
            a = result.agreement[name]
            rho = "—" if a.spearman is None else f"{a.spearman:.2f}".replace("-", "−")
            lines.append(f"| {label} | {a.n} | {rho} | {a.mean_abs_diff:.2f} | {a.within_one:.0%} "
                         f"| {_f(a.bias)} |")
        if GEMINI in result.agreement and PIPELINE in result.agreement and GOOGLE in result.agreement:
            others = (result.agreement[PIPELINE].bias + result.agreement[GOOGLE].bias) / 2
            lines += ["", f"Self-preference check: rater − owner on Gemini-translated items "
                      f"{_f(result.agreement[GEMINI].bias)}, on the other systems' items {_f(others)} "
                      "(mean of the two). A shared item counts for pipeline and Gemini; the anchors are "
                      "rated twice by both."]
    return "\n".join(lines)


START, END = "<!-- translation-rating-result:start -->", "<!-- translation-rating-result:end -->"
TABLE_START, TABLE_END = "<!-- baselines-table:start -->", "<!-- baselines-table:end -->"
README_ROWS = {PIPELINE: "| Talk Autopsy", GOOGLE: "| Whisper + Google Translate",
               GEMINI: "| Gemini translation"}


def replace_between(text: str, start: str, end: str, body: str) -> str:
    if text.count(start) != 1 or text.count(end) != 1 or text.index(start) > text.index(end):
        raise RatingError(f"markers {start} / {end} missing or repeated")
    head, rest = text.split(start)
    _, tail = rest.split(end)
    return f"{head}{start}\n{body}\n{end}{tail}"


def readme_cells(result: BaselineResult) -> dict[str, str]:
    cells = {PIPELINE: f"**{result.means[PIPELINE]:.2f}**"}
    for system in (GEMINI, GOOGLE):
        diff, interval = result.differences[system]
        cells[system] = f"{result.means[system]:.2f} (pipeline {_f(diff)}, {_interval(interval)})"
    if result.drift_flag:
        cells[GOOGLE] += f"; {drift_note(result)}"
    return cells


def update_readme_table(text: str, result: BaselineResult) -> str:
    """The last cell of the pipeline, Google Translate and Gemini rows inside the marked table."""
    if text.count(TABLE_START) != 1 or text.count(TABLE_END) != 1:
        raise RatingError("README baselines-table markers missing or repeated")
    head, rest = text.split(TABLE_START)
    table, tail = rest.split(TABLE_END)
    cells, lines = readme_cells(result), table.split("\n")
    for system, prefix in README_ROWS.items():
        hits = [i for i, line in enumerate(lines) if line.startswith(prefix)]
        if len(hits) != 1:
            raise RatingError(f"README table: {len(hits)} rows start with {prefix!r}")
        row = lines[hits[0]].rstrip()
        if not row.endswith("|"):
            raise RatingError(f"README table: row {prefix!r} does not end with '|'")
        # Split on bare '|' so empty cells ("| |") keep their place.
        parts = row[:-1].split("|")
        parts[-1] = f" {cells[system]} "
        lines[hits[0]] = "|".join(parts) + "|"
    return f"{head}{TABLE_START}{chr(10).join(lines)}{TABLE_END}{tail}"
