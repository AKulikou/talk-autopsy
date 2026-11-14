"""
Reading speed of Russian subtitles (D1 numbers) and the causes of the fast cues.
Evaluation tooling for docs/experiments.md (T7 baseline, reading speed note);
it changes nothing in subtitle assembly. Prints counts and timecodes only, never text.

    uv run python scripts/reading_speed.py <run>.ru.srt [...]           # D1 numbers per file
    uv run python scripts/reading_speed.py --causes <run>.ru.srt [...]  # + causes (needs <run>.en.srt)

Per file: cues, share above 17, 20 and 25 characters per second (`metrics.reading_speed`),
the maximum, cues shorter than 1 s. Causes of each cue above 20 cps, in this order:
`short cue` (shown less than 1.0 s), `fast speech` (the English subtitles of the same
time span already need more than 17 cps), `longer Russian` (the Russian text is more
than 15% longer than the English of the same span), otherwise `other`. The English of a
span is the English cues' characters weighted by their time overlap with the Russian cue.
"""

import argparse
import re
import sys
from pathlib import Path

from talk_autopsy import metrics

_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{3}) --> (\d+):(\d\d):(\d\d)[,.](\d{3})")
SHORT_S = 1.0
FAST_EN_CPS = 17.0
LONGER = 1.15
CAUSE_CPS = 20.0


def parse_srt(text: str) -> list[tuple[str, float, float]]:
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = block.split("\n")
        for k, line in enumerate(lines):
            if match := _TIME.search(line):
                g = [int(x) for x in match.groups()]
                start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
                end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
                cues.append((" ".join(" ".join(lines[k + 1:]).split()), start, end))
                break
    return cues


def shares(cues: list[tuple[str, float, float]]) -> dict:
    speeds = metrics.reading_speed(cues).cps
    n = len(speeds)
    return {"cues": n, "above_17": sum(s > 17 for s in speeds) / n,
            "above_20": sum(s > 20 for s in speeds) / n, "above_25": sum(s > 25 for s in speeds) / n,
            "max": max(speeds),
            "under_1s": sum(1 for _, a, b in cues if b - a < SHORT_S)}


def english_chars(english: list[tuple[str, float, float]], start: float, end: float) -> float:
    total = 0.0
    for text, a, b in english:
        overlap = min(b, end) - max(a, start)
        if overlap > 0 and b > a:
            total += len(text) * overlap / (b - a)
    return total


def causes(russian: list[tuple[str, float, float]], english: list[tuple[str, float, float]]) -> dict:
    found: dict[str, list[str]] = {"short cue": [], "fast speech": [], "longer Russian": [], "other": []}
    for text, start, end in russian:
        duration = end - start
        if len(text) / duration <= CAUSE_CPS:
            continue
        en = english_chars(english, start, end)
        if duration < SHORT_S:
            cause = "short cue"
        elif en / duration > FAST_EN_CPS:
            cause = "fast speech"
        elif en and len(text) / en > LONGER:
            cause = "longer Russian"
        else:
            cause = "other"
        found[cause].append(f"{int(start // 60):02d}:{start % 60:04.1f}")
    return found


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("srt", nargs="+", type=Path)
    parser.add_argument("--causes", action="store_true")
    args = parser.parse_args(argv)
    for path in args.srt:
        cues = parse_srt(path.read_text(encoding="utf-8"))
        s = shares(cues)
        print(f"{path.name}: {s['cues']} cues; above 17 cps {s['above_17']:.1%}, "
              f"above 20 {s['above_20']:.1%}, above 25 {s['above_25']:.1%}; max {s['max']:.1f}; "
              f"shorter than 1 s {s['under_1s']}")
        if args.causes:
            en_path = path.with_name(path.name.replace(".ru.srt", ".en.srt"))
            found = causes(cues, parse_srt(en_path.read_text(encoding="utf-8")))
            fast = sum(len(v) for v in found.values())
            for cause, times in sorted(found.items(), key=lambda kv: -len(kv[1])):
                print(f"  {cause}: {len(times)} of {fast} cues above {CAUSE_CPS:.0f} cps; "
                      f"e.g. {', '.join(times[:3])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
