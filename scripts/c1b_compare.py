"""
C1b: automatic comparison of translation-v2 and translation-v3 on the same
sentence units (docs/experiments.md, C1b). No API call.

    uv run python scripts/c1b_compare.py <name> <v2 ru.json> <v2 glossary> \
        <v3 ru.json> <v3 glossary> <out.json>

Per version: Latin-script words per 100 Cyrillic words (split into words of
the v3 glossary's keep terms and other words; the same split for both
versions), how each of the owner's example terms is rendered (first matching
pattern per unit), keep-term preservation (units whose English has a v3 keep
term, share whose translation has it in Latin), marker warnings, reading speed
(share of cues above 17 characters per second), cost.
"""

import json
import re
import sys
from pathlib import Path

from talk_autopsy import metrics, subtitles
from talk_autopsy.glossary import load_glossary
from talk_autopsy.segments import load_transcript

LATIN = re.compile(r"[A-Za-z][A-Za-z0-9'+#.\-]*")
CYRILLIC = re.compile(r"[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)*")

# Owner's examples: English pattern (case-insensitive) and renderings in priority order.
EXAMPLES = {
    "computer science": (r"computer science|\bCS\b(?! 183)", [
        ("computer science (Latin)", r"computer science"), ("информатика", r"информатик")]),
    "tokens": (r"(?<!thinking )\btokens?\b", [
        ("tokens (Latin)", r"\btokens?\b"), ("токен-", r"токен")]),
    "thinking tokens": (r"thinking tokens?", [
        ("thinking tokens (Latin)", r"thinking tokens?"), ("токены рассуждений", r"токен\w* рассужден"),
        ("other токен-", r"токен")]),
    "frontier": (r"\bfrontier\b", [
        ("frontier-… (loan)", r"frontier-|фронтир"), ("frontier (Latin)", r"\bfrontier\b"),
        ("передов-", r"передов")]),
    "compute": (r"\bcompute\b", [
        ("вычислительные мощности (compute)", r"вычислительн\w* мощност\w* \(compute\)"),
        ("вычислительные мощности", r"вычислительн\w* мощност|вычислительн\w* ресурс"),
        ("compute (Latin)", r"\bcompute\b")]),
    "inference": (r"\binference\b", [
        ("… (inference)", r"\(inference\)"), ("запуск моделей", r"запуск\w* модел"),
        ("inference (Latin)", r"\binference\b"), ("инференс", r"инференс")]),
    "alignment": (r"\balignment\b", [
        ("… (alignment)", r"\(alignment\)"), ("согласование", r"согласован"),
        ("alignment (Latin)", r"\balignment\b"), ("выравнивание", r"выравнива")]),
    "prompt": (r"\bprompt", [
        ("промпт-/промптнуть", r"промпт"), ("prompt (Latin)", r"\bprompt"), ("запрос-", r"запрос"),
        ("вопрос-", r"вопрос")]),
    "emergent": (r"\bemergent", [
        ("эмерджентн-", r"эмерджентн"), ("emergent (Latin)", r"\bemergen"),
        ("появляются / возникают", r"появля|возника")]),
    "Dictation": (r"\bdictation\b", [
        ("Dictation (голосовой ввод)", r"dictation \(голосов"), ("Dictation (Latin)", r"\bdictation\b"),
        ("диктовка", r"диктовк"), ("голосовой ввод", r"голосов\w* ввод")]),
}


def english(segment) -> str:
    return " ".join(w.text for w in segment.words or []) or ""


def keep_words(glossary) -> set[str]:
    return {w.casefold() for e in glossary.entries if e.effective_policy() == "keep"
            for w in LATIN.findall(e.term)}


def measure(transcript_path: Path, glossary_path: Path, keep: set[str], keep_terms: list[str]) -> dict:
    t = load_transcript(transcript_path)
    glossary = load_glossary(glossary_path)
    latin_keep = latin_other = cyr = 0
    other_words: dict[str, int] = {}
    for s in t.segments:
        cyr += len(CYRILLIC.findall(s.text))
        for w in LATIN.findall(s.text):
            if w.casefold().strip(".-") in keep:
                latin_keep += 1
            else:
                latin_other += 1
                other_words[w.casefold()] = other_words.get(w.casefold(), 0) + 1
    examples = {}
    for name, (en_pattern, renderings) in EXAMPLES.items():
        counts: dict[str, int] = {}
        for s in t.segments:
            if not re.search(en_pattern, english(s), re.I):
                continue
            label = next((lab for lab, pat in renderings if re.search(pat, s.text, re.I)), "other")
            counts[label] = counts.get(label, 0) + 1
        if counts:
            examples[name] = counts
    preserved = occurring = 0
    missing = []
    for s in t.segments:
        source = english(s)
        for term in keep_terms:
            if metrics.term_accuracy(source, source, [term]).rows:
                occurring += 1
                if term.casefold() in s.text.casefold():
                    preserved += 1
                else:
                    missing.append(f"{s.id}: {term}")
    cues = subtitles.build_cues(t, glossary)
    report = subtitles.report(cues)
    tr = t.meta.get("translation", {})
    return {
        "units": len(t.segments), "failed_blocks": len(tr.get("failed_blocks") or []),
        "cyrillic_words": cyr, "latin_keep_words": latin_keep, "latin_other_words": latin_other,
        "latin_keep_per_100": round(100 * latin_keep / cyr, 2),
        "latin_other_per_100": round(100 * latin_other / cyr, 2),
        "top_other_latin": sorted(other_words.items(), key=lambda kv: -kv[1])[:15],
        "examples": examples,
        "keep_occurrences": occurring, "keep_preserved": preserved,
        "keep_share": round(preserved / occurring, 4) if occurring else None, "keep_missing": missing[:20],
        "marker_warnings": len(tr.get("marker_warnings") or []) if "marker_warnings" in tr else None,
        "cues": report.cues, "share_above_17_cps": round(report.share_above_17, 4),
        "share_above_25_cps": round(report.share_above_25, 4),
        "cost_usd": tr.get("cost_usd"), "prompt": tr.get("prompt_version"),
    }


def main() -> None:
    name, v2, g2, v3, g3, out = sys.argv[1:7]
    g3_glossary = load_glossary(Path(g3))
    keep = keep_words(g3_glossary)
    keep_terms = [e.term for e in g3_glossary.entries if e.effective_policy() == "keep"]
    result = {"name": name, "v2": measure(Path(v2), Path(g2), keep, keep_terms),
              "v3": measure(Path(v3), Path(g3), keep, keep_terms)}
    Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
