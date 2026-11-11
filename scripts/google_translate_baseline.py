"""
Whisper + Google Translate baseline on the comparison set (docs/experiments.md,
Baselines on one set): the raw faster-whisper transcript of each fragment,
sentence units, Google Cloud Translation API (Basic, v2), Russian .srt/.ass.
With T6 it also builds the 12 Google Translate items of the translation rating
(docs/experiments.md, Translation rating): the raw Whisper words inside each
sampled unit's time span, translated in one request -> T6-units.json.

    uv run python scripts/google_translate_baseline.py                  # T2 and T6 + the T6 rating items
    uv run python scripts/google_translate_baseline.py --fragment T2 --lang ru
    uv run python scripts/google_translate_baseline.py --count-only     # characters, no request

Reads work/<F>/raw.whisper-large-v3.json, writes work/baselines/google-translate/<F>/
(git-ignored). Every character is counted before the first request; above --max-chars
nothing is sent. Without GOOGLE_TRANSLATE_API_KEY in .env it prints the setup steps and
exits 0 without a request. Prints counts and costs only, never text or the key.
"""

import argparse
import sys
from pathlib import Path

from talk_autopsy import config, translation_check, units
from talk_autopsy.baselines import google_translate as gt
from talk_autopsy.segments import load_transcript

WORK = config.PROJECT_ROOT / "work"
RATING_FRAGMENT = "T6"
DEFAULT_SAMPLE = WORK / "T6" / "translation-check" / "sample.json"
DEFAULT_FIXED = WORK / "T6" / "run-pipeline-v3" / "fixed.json"


def rating_unit_texts(raw_path: Path, sample_path: Path, fixed_path: Path) -> list[dict]:
    """
    The sampled units of the translation check with their raw Whisper text (the
    pre-registered mapping, `gt.words_by_span`): spans of the pipeline's sentence
    units rebuilt from the run's fixed.json, whose English must equal the sample's.
    """
    sampled = translation_check.load_sample(sample_path)
    pipeline_units = units.make_units(load_transcript(fixed_path))
    by_id = {s.id: s for s in pipeline_units.segments}
    for item in sampled:
        unit = by_id.get(item.unit)
        if unit is None or unit.text.strip() != item.english:
            raise ValueError(f"unit {item.unit}: the run's fixed.json does not rebuild the sampled English")
    spans = [(s.id, s.start, s.end) for s in pipeline_units.segments]
    whisper = gt.words_by_span(load_transcript(raw_path), spans)
    empty = [i.unit for i in sampled if not whisper[i.unit]]
    if empty:
        raise ValueError(f"no raw Whisper words in sampled units {empty}")
    return [{"unit": i.unit, "start": by_id[i.unit].start, "end": by_id[i.unit].end,
             "whisper_en": whisper[i.unit]} for i in sampled]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fragment", action="append", help="fragment ID (repeatable); default T2 and T6")
    parser.add_argument("--lang", default="ru", help="target language code (default ru)")
    parser.add_argument("--out", type=Path, default=WORK / "baselines" / "google-translate")
    parser.add_argument("--max-chars", type=int, default=100_000,
                        help="send nothing if the run needs more characters than this (default 100,000)")
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE, help="the translation check's sample")
    parser.add_argument("--fixed", type=Path, default=DEFAULT_FIXED, help="the T6 run's fixed.json")
    parser.add_argument("--no-rating-units", action="store_true", help="skip the T6 rating items")
    parser.add_argument("--count-only", action="store_true", help="count characters, send nothing")
    args = parser.parse_args(argv)
    fragments = args.fragment or ["T2", "T6"]

    config.load_environment()
    key = gt.api_key()
    if key is None and not args.count_only:
        print(gt.SETUP_STEPS)
        return 0
    raws = {f: WORK / f / "raw.whisper-large-v3.json" for f in fragments}
    missing = [str(p) for p in raws.values() if not p.is_file()]
    if missing:
        print(f"Error: no raw Whisper transcript: {', '.join(missing)}", file=sys.stderr)
        return 1

    # Everything that will be sent, counted before the first request.
    unit_transcripts = {f: units.make_units(load_transcript(raw)) for f, raw in raws.items()}
    planned = {f: sum(len(t) for t in gt.unit_texts(u)) for f, u in unit_transcripts.items()}
    rating = None
    if RATING_FRAGMENT in fragments and not args.no_rating_units:
        try:
            rating = rating_unit_texts(raws[RATING_FRAGMENT], args.sample, args.fixed)
        except (OSError, ValueError) as e:
            print(f"Error: rating items: {e}", file=sys.stderr)
            return 1
        planned[f"{RATING_FRAGMENT} rating units"] = sum(len(r["whisper_en"]) for r in rating)
    total = sum(planned.values())
    print("Planned characters: " + ", ".join(f"{k} {v}" for k, v in planned.items()) + f"; total {total}")
    if total > args.max_chars:
        print(f"Error: {total} characters > --max-chars {args.max_chars}; nothing sent", file=sys.stderr)
        return 1
    if args.count_only:
        return 0

    caller = _capped(gt.http_caller(key), args.max_chars)
    sent = 0
    try:
        for fragment, unit_transcript in unit_transcripts.items():
            result, srt = gt.run_units(unit_transcript, args.out / fragment, fragment, args.lang, caller)
            sent += result.characters
            print(f"{fragment}: {result.units} units, {result.characters} characters in {result.requests} "
                  f"request(s) -> {srt}")
        if rating is not None:
            texts = [r["whisper_en"] for r in rating]
            answer = caller(texts, "en", args.lang)
            if len(answer) != len(texts):
                raise gt.GoogleTranslateError(f"sent {len(texts)} texts, got {len(answer)} translations")
            sent += sum(len(t) for t in texts)
            path = args.out / f"{RATING_FRAGMENT}-units.json"
            translation_check.write_json(path, {
                "version": gt.VERSION, "rule": "docs/experiments.md, Translation rating (2026-11-11)",
                "engine": "Google Cloud Translation API Basic (v2), format text",
                "source_lang": "en", "target_lang": args.lang, "characters": sum(len(t) for t in texts),
                "items": [{**r, "translation": a.strip()} for r, a in zip(rating, answer, strict=True)]})
            print(f"{RATING_FRAGMENT} rating units: {len(texts)} items, {sum(len(t) for t in texts)} "
                  f"characters in 1 request -> {path}")
    except gt.GoogleTranslateError as e:
        print(f"Error: {e} (sent {sent} characters before it)", file=sys.stderr)
        return 1
    print(f"Total {sent} characters (free tier {gt.FREE_CHARS_PER_MONTH:,} per month; beyond it "
          f"{sent * gt.USD_PER_MILLION_CHARS / 1_000_000:.4f} USD)")
    return 0


def _capped(caller: gt.Caller, remaining: int) -> gt.Caller:
    """The caller, refusing a request that would take the run past --max-chars."""
    budget = {"left": remaining}

    def call(texts: list[str], source: str, target: str) -> list[str]:
        size = sum(len(t) for t in texts)
        if size > budget["left"]:
            raise gt.GoogleTranslateError(f"--max-chars reached ({size} more characters would exceed it)")
        budget["left"] -= size
        return caller(texts, source, target)

    return call


if __name__ == "__main__":
    sys.exit(main())
