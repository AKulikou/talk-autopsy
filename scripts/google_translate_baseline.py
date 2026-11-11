"""
Whisper + Google Translate baseline on the comparison set (docs/experiments.md,
Baselines on one set): the raw faster-whisper transcript of each fragment,
sentence units, Google Cloud Translation API (Basic, v2), Russian .srt/.ass.

    uv run python scripts/google_translate_baseline.py                  # T2 and T6
    uv run python scripts/google_translate_baseline.py --fragment T2 --lang ru

Reads work/<F>/raw.whisper-large-v3.json, writes work/baselines/google-translate/<F>/
(git-ignored). Without GOOGLE_TRANSLATE_API_KEY in .env it prints the setup steps and
exits 0 without a request. Prints counts and costs only, never text or the key.
"""

import argparse
import sys
from pathlib import Path

from talk_autopsy import config
from talk_autopsy.baselines import google_translate as gt

WORK = config.PROJECT_ROOT / "work"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fragment", action="append", help="fragment ID (repeatable); default T2 and T6")
    parser.add_argument("--lang", default="ru", help="target language code (default ru)")
    parser.add_argument("--out", type=Path, default=WORK / "baselines" / "google-translate")
    parser.add_argument("--max-chars", type=int, default=100_000,
                        help="stop before sending more characters than this (default 100,000)")
    args = parser.parse_args(argv)
    fragments = args.fragment or ["T2", "T6"]

    config.load_environment()
    key = gt.api_key()
    if key is None:
        print(gt.SETUP_STEPS)
        return 0
    raws = {f: WORK / f / "raw.whisper-large-v3.json" for f in fragments}
    missing = [str(p) for p in raws.values() if not p.is_file()]
    if missing:
        print(f"Error: no raw Whisper transcript: {', '.join(missing)}", file=sys.stderr)
        return 1
    caller = gt.http_caller(key)
    sent = 0
    for fragment, raw in raws.items():
        try:
            result, srt = gt.run(raw, args.out / fragment, fragment, args.lang,
                                 _capped(caller, args.max_chars - sent))
        except gt.GoogleTranslateError as e:
            print(f"Error: {fragment}: {e}", file=sys.stderr)
            return 1
        sent += result.characters
        print(f"{fragment}: {result.units} units, {result.characters} characters in {result.requests} "
              f"request(s) -> {srt}")
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
