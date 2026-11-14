"""
Subtitle lines left in English in finished runs, from the saved files (no model call).

    uv run python scripts/left_english_report.py <work folder> [<work folder> ...] [--lang ru] [--text]

Per run: (1) segments the translation reverted to English after both answers were refused
(`meta.translation.reverted_segments`, with the checks' own problem lines) and segments of failed
blocks; (2) final lines the "left in English" check (`translate.left_in_english`, the run's own
glossary) flags. Prints counts and, per line, the start time and the reason; `--text` adds the line
itself (local console only: never paste it into tracked files).
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s // 60 % 60:02}:{s % 60:02}" if s >= 3600 else f"{s // 60}:{s % 60:02}"


def report(work: Path, lang: str, show_text: bool) -> dict:
    from talk_autopsy.glossary import load_glossary
    from talk_autopsy.segments import load_transcript
    from talk_autopsy.translate import left_in_english

    translated = load_transcript(work / f"{lang}.json")
    glossary = load_glossary(work / "glossary.md")
    meta = translated.meta.get("translation") or {}
    by_id = {s.id: s for s in translated.segments}
    rows = []
    for block in meta.get("reverted_segments") or []:
        problems = block.get("problems") or {}
        for i in block.get("ids") or []:
            reason = "; ".join(problems.get(str(i)) or problems.get(i) or []) or "reverted"
            rows.append((i, "reverted", reason))
    for block in meta.get("failed_blocks") or []:
        for i in block.get("ids") or []:
            why = str(block.get("problems") or block.get("error") or "failed")[:200]
            rows.append((i, "failed block", why))
    reverted_ids = {i for i, _, _ in rows}
    flagged = 0
    for s in translated.segments:
        english = " ".join(w.text for w in s.words or [])
        if s.id not in reverted_ids and english and left_in_english(english, s.text, glossary):
            flagged += 1
            rows.append((s.id, "flagged", "left in English check on the final line"))
    rows.sort()
    print(f"== {work.name}: {len(translated.segments)} lines; reverted {len(reverted_ids)}, "
          f"flagged {flagged}, total left in English {len(reverted_ids) + flagged}")
    for i, kind, reason in rows:
        s = by_id.get(i)
        start = clock(s.start) if s else "?"
        line = f"   {start}  [{kind}] id {i}: {reason[:160]}"
        if show_text and s:
            english = " ".join(w.text for w in s.words or [])[:150]
            line += f"\n            EN: {english}\n            RU: {s.text[:150]}"
        print(line)
    return {"lines": len(translated.segments), "reverted": len(reverted_ids), "flagged": flagged}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("work", nargs="+", type=Path)
    parser.add_argument("--lang", default="ru")
    parser.add_argument("--text", action="store_true")
    args = parser.parse_args()
    for work in args.work:
        report(work, args.lang, args.text)


if __name__ == "__main__":
    main()
