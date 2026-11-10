"""
Glossary of one video: the terms correction must get right and how translation
renders them.

Format: a Markdown table (files `T<n>.glossary.md`), chosen over YAML because the
glossaries are published and read on GitHub, and because it needs no extra
dependency. Text before and after the table (headings, notes) is ignored; only
the first table counts.

    | term        | keep | ru                     | notes                     |
    |-------------|------|------------------------|---------------------------|
    | Claude Code | yes  |                        | product name              |
    | subagent    | no   | субагент               |                           |
    | worktree    | no   | рабочее дерево (worktree) | git term; keep English in brackets |

Columns:
- `term` (required): the English term as it should appear in the corrected transcript.
- `keep` (required): yes = keep the term untranslated; no = translate it.
- any column named by a language code (`ru`, `de`, ...): the agreed translation;
  an empty cell means none agreed yet.
- `notes` (optional): context for the model and for review.

A literal `|` inside a cell is written as `\\|`.
"""

import re
from pathlib import Path

from pydantic import BaseModel, Field

from talk_autopsy.config import ConfigurationError, check_lang

REQUIRED_COLUMNS = ("term", "keep")
NOTES_COLUMN = "notes"
_YES = {"yes", "y", "true"}
_NO = {"no", "n", "false"}
_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")


class GlossaryError(ValueError):
    """The glossary file does not follow the documented table format."""


class GlossaryEntry(BaseModel):
    term: str
    keep: bool
    translations: dict[str, str] = Field(default_factory=dict)  # language code -> agreed translation
    notes: str = ""


class Glossary(BaseModel):
    entries: list[GlossaryEntry] = Field(default_factory=list)

    def languages(self) -> list[str]:
        """Language codes that have at least one translation."""
        return sorted({lang for entry in self.entries for lang in entry.translations})

    def find(self, term: str) -> GlossaryEntry | None:
        wanted = term.casefold()
        return next((entry for entry in self.entries if entry.term.casefold() == wanted), None)


def _cells(line: str) -> list[str]:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", body)]


def parse_glossary(text: str) -> Glossary:
    """Parse the first Markdown table in `text`; GlossaryError names the line of any problem."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip().startswith("|")), None)
    if start is None:
        return Glossary()
    header = [cell.lower() for cell in _cells(lines[start])]
    for column in REQUIRED_COLUMNS:
        if column not in header:
            raise GlossaryError(f"line {start + 1}: the table needs a '{column}' column, got {header}")
    if len(set(header)) != len(header):
        raise GlossaryError(f"line {start + 1}: duplicate column names {header}")
    languages = {}
    for column in header:
        if column in (*REQUIRED_COLUMNS, NOTES_COLUMN):
            continue
        try:
            languages[column] = check_lang(column)
        except ConfigurationError:
            raise GlossaryError(
                f"line {start + 1}: column {column!r} is neither term, keep, notes nor a language code"
            ) from None
    if start + 1 >= len(lines) or not all(_SEPARATOR_CELL.match(c) for c in _cells(lines[start + 1])):
        raise GlossaryError(f"line {start + 2}: expected the |---|---| separator row under the header")

    entries: list[GlossaryEntry] = []
    seen: dict[str, int] = {}
    for number, line in enumerate(lines[start + 2:], start=start + 3):
        if not line.strip().startswith("|"):
            break  # the table ends at the first line that is not a row
        cells = _cells(line)
        if len(cells) != len(header):
            raise GlossaryError(f"line {number}: {len(cells)} cells, the header has {len(header)}")
        row = dict(zip(header, cells, strict=True))
        term = row["term"]
        if not term:
            raise GlossaryError(f"line {number}: empty term")
        keep = row["keep"].lower()
        if keep not in _YES | _NO:
            raise GlossaryError(f"line {number}: keep must be yes or no, got {row['keep']!r}")
        key = term.casefold()
        if key in seen:
            raise GlossaryError(f"line {number}: {term!r} is already defined on line {seen[key]}")
        seen[key] = number
        entries.append(GlossaryEntry(
            term=term,
            keep=keep in _YES,
            translations={languages[c]: row[c] for c in languages if row[c]},
            notes=row.get(NOTES_COLUMN, ""),
        ))
    return Glossary(entries=entries)


def load_glossary(path: Path) -> Glossary:
    return parse_glossary(Path(path).read_text(encoding="utf-8"))


def to_markdown(glossary: Glossary, languages: list[str] | None = None) -> str:
    """The glossary as a table in the documented format (columns: term, keep, languages, notes)."""
    langs = languages if languages is not None else glossary.languages()

    def cell(text: str) -> str:
        return text.replace("|", "\\|")

    rows = ["| " + " | ".join(["term", "keep", *langs, NOTES_COLUMN]) + " |",
            "|" + "---|" * (len(langs) + 3)]
    for entry in glossary.entries:
        values = [entry.term, "yes" if entry.keep else "no",
                  *(entry.translations.get(lang, "") for lang in langs), entry.notes]
        rows.append("| " + " | ".join(cell(v) for v in values) + " |")
    return "\n".join(rows) + "\n"


# --- combining user glossaries (--glossary, --domain, glossaries/local/) -----------------


def combine(glossaries: list[Glossary]) -> Glossary:
    """
    One user glossary from several, in priority order: an entry from an earlier
    glossary wins over a later one with the same term (case-insensitive).
    """
    combined = Glossary()
    for glossary in glossaries:
        for entry in glossary.entries:
            if combined.find(entry.term) is None:
                combined.entries.append(entry)
    return combined


def domain_paths(domain: str, glossaries_dir: Path, local_dir: Path) -> list[Path]:
    """
    `--domain <name>` -> glossaries/<name>.md, then glossaries/local/<name>.md
    if it exists (personal additions such as people's names, git-ignored).
    GlossaryError if the domain glossary does not exist.
    """
    name = domain.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        raise GlossaryError(f"domain {domain!r}: use letters, digits, - and _")
    main = Path(glossaries_dir) / f"{name}.md"
    if not main.is_file():
        folder = Path(glossaries_dir)
        known = sorted(p.stem for p in folder.glob("*.md")) if folder.is_dir() else []
        raise GlossaryError(f"no domain glossary {main}; known domains: {', '.join(known) or 'none'}")
    local = Path(local_dir) / f"{name}.md"
    return [main, local] if local.is_file() else [main]

