"""
The pre-registered translation check of a hold-out run (docs/experiments.md, T6
protocol). Four steps; each writes into OUT (work/T6/translation-check by default)
and prints counts and costs only, never text.

    uv run python scripts/translation_check.py sample --fixed <run>/fixed.json --translated <run>/ru.json
    uv run python scripts/translation_check.py baseline [--model gemini-4-carbon]  # paid Gemini, 12 requests
    uv run python scripts/translation_check.py rating   # -> work/T6/C1-T6-rating.md + work/T6/C1-T6-key.json
    uv run python scripts/translation_check.py score    # after the owner filled C1-T6-rating.md
"""

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from talk_autopsy import config, translation_check
from talk_autopsy.segments import load_transcript

DEFAULT_OUT = config.WORK_DIR / "T6" / "translation-check"
DEFAULT_RATING = config.WORK_DIR / "T6" / "C1-T6-rating.md"
DEFAULT_KEY = config.WORK_DIR / "T6" / "C1-T6-key.json"


def _sampled(path: Path) -> list[translation_check.Item]:
    return [translation_check.Item(**i) for i in json.loads(path.read_text(encoding="utf-8"))["items"]]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=("sample", "baseline", "rating", "score"))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--rating", type=Path, default=DEFAULT_RATING, help="the owner's rating file")
    parser.add_argument("--key", type=Path, default=DEFAULT_KEY, help="the key of the rating file")
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


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
