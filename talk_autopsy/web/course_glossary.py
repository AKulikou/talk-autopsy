"""
The course glossary (v1.2, C2b): lessons 2..N of a course reuse the terms of the
earlier lessons, and the Russian renderings those lessons actually used, so a
term is written and translated the same way in every lesson.

It reaches the pipeline only through the user-glossary input (F5): no frozen
module changes. What F5 carries and how it is filled here:

- `term`, `keep`: every term of the finished lessons' auto-glossaries
  (`glossary.auto.md` in the lesson's work folder), as the auto-glossary
  classified it.
- `ru` (the agreed translation, which the translation step must use and its
  check enforces): only for a translated term whose auto-glossary suggestion
  (`suggested ru: ...` in its notes) **was used** in that lesson's
  translation, in at least one sentence unit whose English contains the term
  (the translation check's own inflection rule, `translate._stem_present`).
  A suggestion the lesson did not use is left in the notes, as the
  auto-glossary wrote it: F5 has no "rendering seen" column, and an agreed
  translation the lesson itself did not follow would only add refusals.
- `notes`: the auto-glossary's notes, plus `course: lesson N` (where it came from).

Precedence (`user_glossary.run_glossary`, earlier files win): the domain
dictionary and its local additions (the owner's own) first, the course
glossary last; the run's user glossary then wins over the lesson's own
auto-glossary on a conflict.

Files: `<course>/course-glossary.md` (what the owner opens; regenerated after
every finished lesson) and `<course>/.talk-autopsy/course-glossary.json` (the
terms with their counts, the lessons merged). Each lesson keeps the exact input
it ran with in `<lesson>/.talk-autopsy/course-glossary.md`, so re-running a
finished lesson gives the same request (the cache holds) even after later
lessons grew the course glossary.

Cap: MAX_TERMS terms, the most frequent first (occurrences in the corrected
English text of the lessons so far, summed), ties in the order the terms first
appeared.
"""

import json
import logging
import re
import shutil
from pathlib import Path

from talk_autopsy import youtube
from talk_autopsy.glossary import Glossary, GlossaryEntry, GlossaryError, load_glossary, to_markdown
from talk_autopsy.segments import load_transcript

logger = logging.getLogger(__name__)

DEFAULT_ON = False  # C2b decides (docs/experiments.md); until then the option is off by default
MAX_TERMS = 150
FILE_NAME = "course-glossary.md"
STATE_NAME = "course-glossary.json"
_SUGGESTED = "suggested {lang}: "


def course_file(course_folder: Path) -> Path:
    return Path(course_folder) / FILE_NAME


def snapshot_file(course_folder: Path, lesson_folder_name: str) -> Path:
    return youtube.service_dir(Path(course_folder) / lesson_folder_name) / FILE_NAME


def inputs_for(course_folder: Path, lesson_folder_name: str, write: bool = False) -> list[Path]:
    """
    The course-glossary file a lesson runs with: its own snapshot when it ran before, else the
    course glossary as it is now (copied into the lesson's snapshot when `write`); [] when empty.
    """
    snapshot = snapshot_file(course_folder, lesson_folder_name)
    if snapshot.is_file():
        return [snapshot]
    current = course_file(course_folder)
    if not current.is_file():
        return []
    if not write:
        return [current]
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    youtube.hide(snapshot.parent)
    shutil.copyfile(current, snapshot)
    return [snapshot]


def _state(course_folder: Path) -> dict:
    path = youtube.service_dir(Path(course_folder)) / STATE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("terms"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"format": 1, "lessons": [], "terms": []}


def summary(course_folder: Path) -> dict:
    state = _state(course_folder)
    path = course_file(course_folder)
    return {"terms": min(len(state["terms"]), MAX_TERMS), "lessons": len(state["lessons"]),
            "file": str(path) if path.is_file() else None}


def count(term: str, text: str) -> int:
    """Occurrences of `term` in `text`, case-insensitive, not inside a longer word."""
    pattern = r"(?<![\w.])" + re.escape(term.casefold()) + r"(?![\w])"
    return len(re.findall(pattern, text.casefold()))


def suggestion(entry: GlossaryEntry, lang: str) -> str:
    """The auto-glossary's suggested rendering in `lang` (from its notes), or ""."""
    marker = _SUGGESTED.format(lang=lang)
    for part in entry.notes.split("; "):
        if part.startswith(marker):
            return part[len(marker):].strip()
    return ""


def used_rendering(entry: GlossaryEntry, lang: str, units: list[tuple[str, str]]) -> str:
    """The suggested rendering if a unit with the term in its English used it, else ""."""
    from talk_autopsy.translate import _stem_present  # the translation check's own inflection rule

    rendering = entry.translations.get(lang) or suggestion(entry, lang)
    if entry.keep or not rendering:
        return ""
    for english, target in units:
        if count(entry.term, english) and _stem_present(rendering, target):
            return rendering
    return ""


def lesson_terms(work_dir: Path, lang: str) -> tuple[list[GlossaryEntry], dict[str, int]]:
    """A finished lesson's auto-glossary terms (agreed rendering where it was used) and their counts."""
    auto = Path(work_dir) / "glossary.auto.md"
    if not auto.is_file():
        return [], {}
    glossary = load_glossary(auto)
    fixed = load_transcript(Path(work_dir) / "fixed.json")
    english_text = " ".join(s.text for s in fixed.segments)
    translated = load_transcript(Path(work_dir) / f"{lang}.json")
    units = [(" ".join(w.text for w in s.words or []), s.text) for s in translated.segments]
    entries, counts = [], {}
    for entry in glossary.entries:
        rendering = used_rendering(entry, lang, units)
        notes = entry.notes
        if rendering:  # now agreed: the suggestion note would only repeat it
            notes = "; ".join(p for p in notes.split("; ") if not p.startswith(_SUGGESTED.format(lang=lang)))
        entries.append(GlossaryEntry(term=entry.term, keep=entry.keep,
                                     translations={lang: rendering} if rendering else {}, notes=notes))
        counts[entry.term.casefold()] = count(entry.term, english_text)
    return entries, counts


def after_lesson(course_folder: Path, lesson_folder_name: str, position: int, work_dir: Path,
                 lang: str) -> None:
    """Merge a finished lesson's terms into the course glossary (once per lesson); never raises."""
    try:
        state = _state(course_folder)
        if lesson_folder_name in state["lessons"]:
            return
        entries, counts = lesson_terms(work_dir, lang)
        known = {t["term"].casefold(): t for t in state["terms"]}
        for entry in entries:
            key = entry.term.casefold()
            if key in known:  # the first lesson's classification and rendering stay; counts add up
                known[key]["count"] += counts.get(key, 0)
                if not known[key]["ru"] and entry.translations.get(lang) and not known[key]["keep"]:
                    known[key]["ru"] = entry.translations[lang]
                continue
            item = {"term": entry.term, "keep": entry.keep, "ru": entry.translations.get(lang, ""),
                    "notes": "; ".join(p for p in (entry.notes, f"course: lesson {position}") if p),
                    "count": counts.get(key, 0), "order": len(state["terms"])}
            state["terms"].append(item)
            known[key] = item
        state["lessons"].append(lesson_folder_name)
        state["lang"] = lang
        service = youtube.service_dir(Path(course_folder), create=True)
        (service / STATE_NAME).write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n",
                                          encoding="utf-8")
        write_markdown(course_folder, state, lang)
    except (OSError, ValueError, GlossaryError):
        logger.exception("course glossary not updated after %s", lesson_folder_name)


def chosen_terms(state: dict) -> list[dict]:
    """The terms written to the file: the MAX_TERMS most frequent, ties in first-seen order."""
    ranked = sorted(state["terms"], key=lambda t: (-int(t.get("count", 0)), int(t.get("order", 0))))
    return ranked[:MAX_TERMS]


def write_markdown(course_folder: Path, state: dict, lang: str) -> Path:
    entries = [GlossaryEntry(term=t["term"], keep=bool(t["keep"]),
                             translations={lang: t["ru"]} if t.get("ru") else {}, notes=t.get("notes", ""))
               for t in chosen_terms(state)]
    path = course_file(course_folder)
    if not entries:
        return path
    head = (f"<!-- course glossary: terms of {len(state['lessons'])} finished lesson(s), the {MAX_TERMS} "
            f"most frequent; {lang} = the rendering a lesson used (agreed for the next lessons) -->")
    path.write_text(head + "\n\n" + to_markdown(Glossary(entries=entries), [lang]), encoding="utf-8",
                    newline="\n")
    return path
