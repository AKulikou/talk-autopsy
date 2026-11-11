"""
The pre-registered translation check of a hold-out run (docs/experiments.md, T6
protocol). Four steps; each writes into OUT (work/T6/translation-check by default)
and prints counts and costs only, never text.

    uv run python scripts/translation_check.py sample --fixed <run>/fixed.json --translated <run>/ru.json
    uv run python scripts/translation_check.py baseline [--model gemini-4-carbon]  # paid Gemini, 12 requests
    uv run python scripts/translation_check.py rating   # -> work/T6/C1-T6-rating.md + work/T6/C1-T6-key.json
    uv run python scripts/translation_check.py score    # after the owner filled C1-T6-rating.md

The baselines' rating (docs/experiments.md, Baselines on one set > Translation rating):

    uv run python scripts/translation_check.py rating-2         # -> C1-T6-rating-2.md + C1-T6-key-2.json
    uv run python scripts/translation_check.py rater            # Gemini rates all 38 items (paid; held back)
    uv run python scripts/translation_check.py score-baselines  # after the owner filled C1-T6-rating-2.md
"""

import argparse
import dataclasses
import datetime
import json
import sys
from pathlib import Path

from talk_autopsy import config, translation_check
from talk_autopsy import translation_rating as tr
from talk_autopsy.segments import load_transcript

DEFAULT_OUT = config.WORK_DIR / "T6" / "translation-check"
DEFAULT_RATING = config.WORK_DIR / "T6" / "C1-T6-rating.md"
DEFAULT_KEY = config.WORK_DIR / "T6" / "C1-T6-key.json"
DEFAULT_RATING_2 = config.WORK_DIR / "T6" / "C1-T6-rating-2.md"
DEFAULT_KEY_2 = config.WORK_DIR / "T6" / "C1-T6-key-2.json"
DEFAULT_GOOGLE = config.WORK_DIR / "baselines" / "google-translate" / "T6-units.json"
DEFAULT_RATER = config.WORK_DIR / "T6" / "gemini-rater.json"
DOCS = config.PROJECT_ROOT / "docs" / "experiments.md"
README = config.PROJECT_ROOT / "README.md"


def _sampled(path: Path) -> list[translation_check.Item]:
    return translation_check.load_sample(path)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))  # LF, as the repository keeps Markdown


def _first(rating: Path, key: Path) -> tuple[dict, dict]:
    key1 = json.loads(_read(key))
    return tr.with_units(tr.parse_items(_read(rating)), key1), key1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=("sample", "baseline", "rating", "score", "rating-2", "rater",
                                         "score-baselines"))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--rating", type=Path, default=DEFAULT_RATING, help="the owner's rating file")
    parser.add_argument("--key", type=Path, default=DEFAULT_KEY, help="the key of the rating file")
    parser.add_argument("--rating-2", type=Path, default=DEFAULT_RATING_2, help="the second rating file")
    parser.add_argument("--key-2", type=Path, default=DEFAULT_KEY_2, help="the key of the second file")
    parser.add_argument("--google", type=Path, default=DEFAULT_GOOGLE, help="Google Translate T6 items")
    parser.add_argument("--rater-out", type=Path, default=DEFAULT_RATER, help="the Gemini rater's scores")
    parser.add_argument("--budget", type=float, default=tr.RATER_BUDGET_USD, help="Gemini rater budget, USD")
    parser.add_argument("--no-write", action="store_true", help="score-baselines: print only")
    parser.add_argument("--fixed", type=Path)
    parser.add_argument("--translated", type=Path)
    parser.add_argument("--lang", default="ru")
    parser.add_argument("--model", default=None, help="Gemini model of the baseline (default: JUDGE_MODEL)")
    args = parser.parse_args(argv)
    out = args.out
    sample_path, baseline_path = out / "sample.json", out / "baseline.json"
    rating_path, key_path = args.rating, args.key

    if args.step == "sample":
        if not (args.fixed and args.translated):
            parser.error("sample needs --fixed and --translated")
        items = translation_check.paired_units(load_transcript(args.fixed), load_transcript(args.translated))
        chosen = translation_check.sample(items)
        out.mkdir(parents=True, exist_ok=True)
        record = {"version": translation_check.VERSION, "seed": translation_check.SEED,
                  "units_total": len(items), "items": [dataclasses.asdict(i) for i in chosen]}
        translation_check.write_json(sample_path, record)
        print(f"sampled {len(chosen)} of {len(items)} units (seed {translation_check.SEED}) "
              f"-> {sample_path}")
    elif args.step == "baseline":
        config.load_environment()
        from talk_autopsy.judge import GeminiCaller

        items = _sampled(sample_path)
        model = args.model or config.judge_model()
        caller = GeminiCaller(model, pace_seconds=1.0)
        translations, spent = translation_check.baseline_translations(items, caller, args.lang)
        translation_check.write_json(baseline_path, {
            "model": model, "prompt": translation_check.BASELINE_PROMPT, "cost_usd": spent,
            "translations": {str(k): v for k, v in translations.items()}})
        print(f"baseline: {len(translations)} units, {model}, {spent:.4f} USD -> {baseline_path}")
    elif args.step == "rating":
        items = _sampled(sample_path)
        baseline = {int(k): v for k, v in
                    json.loads(baseline_path.read_text(encoding="utf-8"))["translations"].items()}
        text, key = translation_check.rating_file(items, baseline)
        rating_path.parent.mkdir(parents=True, exist_ok=True)
        rating_path.write_text(text, encoding="utf-8")
        translation_check.write_json(key_path, key)
        print(f"{len(key['items'])} items, shuffles {key['shuffles']} -> {rating_path} (key: {key_path})")
    elif args.step == "rating-2":
        return _rating_2(args)
    elif args.step == "rater":
        return _rater(args)
    elif args.step == "score-baselines":
        return _score_baselines(args)
    else:
        scores = translation_check.parse_scores(rating_path.read_text(encoding="utf-8"))
        key = json.loads(key_path.read_text(encoding="utf-8"))
        result = translation_check.score(scores, key)
        low, high = result.interval
        print(f"units {result.units}: pipeline mean {result.pipeline_mean:.2f}, baseline mean "
              f"{result.baseline_mean:.2f}, difference {result.difference:+.2f} (bootstrap 95% "
              f"[{low:+.2f}, {high:+.2f}]); C1 on this fragment (pipeline mean >= "
              f"{translation_check.C1_TARGET}): {'MET' if result.met else 'NOT MET'}")
    return 0


def _rating_2(args) -> int:
    first, key1 = _first(args.rating, args.key)
    google = {i["unit"]: i["translation"] for i in json.loads(_read(args.google))["items"]}
    text, key2 = tr.rating_file_2(first, key1, google)
    if args.rating_2.exists() and translation_check.parse_scores(_read(args.rating_2)):
        print(f"Error: {args.rating_2} already has scores; not overwritten", file=sys.stderr)
        return 1
    args.rating_2.parent.mkdir(parents=True, exist_ok=True)
    _write(args.rating_2, text)
    translation_check.write_json(args.key_2, key2)
    print(f"{len(key2['items'])} items ({len(key2['anchors'])} anchors), shuffles {key2['shuffles']} "
          f"-> {args.rating_2} (key: {args.key_2})")
    return 0


def _rater(args) -> int:
    """Gemini rates every item of both files; prints counts and cost only, never a score."""
    config.load_environment()
    from talk_autopsy.judge import GeminiCaller

    first, key1 = _first(args.rating, args.key)
    second = tr.with_units(tr.parse_items(_read(args.rating_2)), json.loads(_read(args.key_2)))
    items = tr.rater_items(first, second)
    model = args.model or config.judge_model()
    if args.rater_out.exists():
        record = json.loads(_read(args.rater_out))
        same = ((record["model"], record["version"], set(record["order"]))
                == (model, tr.RATER_VERSION, set(items)))
        if not same:
            print(f"Error: {args.rater_out} is from another model, version or item set", file=sys.stderr)
            return 1
    else:
        record = {"version": tr.RATER_VERSION, "model": model, "temperature": 0, "thinking": "model default",
                  "prompt": tr.RATER_PROMPT, "schema": tr.RATER_SCHEMA, "seed": tr.RATER_SEED,
                  "order": tr.rater_order(list(items)), "cost_usd": 0.0, "items": []}
    caller = GeminiCaller(model, pace_seconds=1.0, temperature=0.0)
    record = tr.rate(items, caller, record, lambda r: translation_check.write_json(args.rater_out, r),
                     args.budget)
    stopped = f"; stopped: {record['stopped']}" if record.get("stopped") else ""
    print(f"rater: {len(record['items'])} of {len(items)} items, {model}, {record['cost_usd']:.4f} USD"
          f"{stopped} -> {args.rater_out} (scores held back until the owner has rated)")
    return 0 if len(record["items"]) == len(items) else 1


def _score_baselines(args) -> int:
    key1, key2 = json.loads(_read(args.key)), json.loads(_read(args.key_2))
    scores1 = translation_check.parse_scores(_read(args.rating))
    scores2 = translation_check.parse_scores(_read(args.rating_2))
    rater = json.loads(_read(args.rater_out)) if args.rater_out.exists() else None
    result = tr.score_baselines(scores1, key1, scores2, key2, rater)
    section = tr.result_markdown(result, datetime.date.today().isoformat())
    if not args.no_write:  # the files first: a console that cannot print the text must not lose them
        _write(DOCS, tr.replace_between(_read(DOCS), tr.START, tr.END, section))
        _write(README, tr.update_readme_table(_read(README), result))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # "−", "…", "≥" on a cp1252 console
    print(section)
    if not args.no_write:
        print(f"written: {DOCS} (Translation rating result), {README} (baselines table)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
