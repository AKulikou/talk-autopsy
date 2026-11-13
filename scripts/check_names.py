"""
De-identification check over every tracked file (`git ls-files`). Two parts:

(a) Local names: the git-ignored list `work/names.local.txt` (one name or phrase
    per line, drawn from the owner's notes; never committed). An entry matches
    as whole words ("Ann" does not match "annotate"), case-insensitively and
    **across line breaks**: the words of an entry may be
    separated by any whitespace, including a newline and the indentation of a
    wrapped Markdown line. A hit prints the file, the line and the entry's
    number, never the entry.
(b) Committed patterns: `scripts/identifying_patterns.txt`, regular expressions
    for generic identifying descriptors (a nationality or region word next to
    "accent", a role at a named company, original file names that look like
    video IDs). Anything specific to one person belongs in the local list, not
    here. Matched case-insensitively over the
    whole file text, so a pattern's `\\s+` also crosses line breaks. A hit prints
    the file, the line and the pattern's number.

    uv run python scripts/check_names.py                     # (a) + (b): local, from scripts/check.ps1
    uv run python scripts/check_names.py --patterns-only     # (b) only: CI (no name list there)
    uv run python scripts/check_names.py --names other.txt   # another local list

Exit code 1 on any hit, 2 if the local list is missing in a run that needs it.
This file, the pattern file and its test are not scanned: they hold the patterns
(whose word lists overlap with the local list) and their test examples.

Allow-list: the "## Sources" section of `data/test-set/README.md` names the
speakers of the published recordings (owner's decision 2026-11-12), so part (a)
skips that one section; part (b) still scans it. Nothing else is allowed.
"""

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_NAMES = Path("work/names.local.txt")
PATTERNS_FILE = Path("scripts/identifying_patterns.txt")
# Files that hold the patterns or their test examples; neither part scans them.
EXEMPT = {"scripts/check_names.py", PATTERNS_FILE.as_posix(), "tests/test_check_names.py"}
# The one place where speaker names are allowed (part (a) only): file -> section heading.
NAMES_ALLOWED_SECTIONS = {"data/test-set/README.md": "## Sources"}


@dataclass(frozen=True)
class Hit:
    file: str
    line: int
    source: str  # "list entry 3" or "pattern 2"


def name_regex(entry: str) -> re.Pattern:
    """An entry's words in order, as whole words, separated by any whitespace (line breaks included)."""
    words = entry.split()
    if not words:
        raise ValueError("empty entry")
    body = r"\s+".join(re.escape(w) for w in words)
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


def read_names(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_patterns(path: Path) -> list[re.Pattern]:
    """One regular expression per line; blank lines and lines starting with `#` are skipped."""
    patterns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            patterns.append(re.compile(line.strip(), re.IGNORECASE))
    return patterns


def blank_section(text: str, heading: str) -> str:
    """`text` with the section under `heading` (up to the next heading of the same or a higher
    level) replaced by spaces, line breaks kept, so that line numbers of later hits stay right."""
    level = len(heading) - len(heading.lstrip("#"))
    lines = text.split("\n")
    out, inside = [], False
    for line in lines:
        if line.strip() == heading:
            inside = True
        elif inside and line.startswith("#"):
            hashes = len(line) - len(line.lstrip("#"))
            if hashes <= level and line[hashes:hashes + 1] == " ":
                inside = False
        out.append(" " * len(line) if inside else line)
    return "\n".join(out)


def find_hits(name: str, text: str, regexes: Iterable[tuple[str, re.Pattern]]) -> list[Hit]:
    hits = []
    for source, regex in regexes:
        for match in regex.finditer(text):
            hits.append(Hit(name, text.count("\n", 0, match.start()) + 1, source))
    return hits


def scan(files: Iterable[str], names: list[str] | None, patterns: list[re.Pattern] | None,
         root: Path = ROOT) -> list[Hit]:
    """Hits of the name list (if given) and the patterns (if given) in the files under `root`."""
    name_regexes = [(f"list entry {i}", name_regex(n)) for i, n in enumerate(names or [], start=1)]
    pattern_regexes = [(f"pattern {i}", p) for i, p in enumerate(patterns or [], start=1)]
    hits: list[Hit] = []
    for name in files:
        if name in EXEMPT:
            continue
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        names_text = text
        if name in NAMES_ALLOWED_SECTIONS:
            names_text = blank_section(text, NAMES_ALLOWED_SECTIONS[name])
        hits += find_hits(name, names_text, name_regexes) + find_hits(name, text, pattern_regexes)
    return hits


def tracked_files(root: Path = ROOT) -> list[str]:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
    return [f for f in listed.decode("utf-8").split(chr(0)) if f]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--names", type=Path, default=DEFAULT_NAMES, help="local name list (git-ignored)")
    parser.add_argument("--patterns-only", action="store_true", help="part (b) only (CI)")
    args = parser.parse_args(argv)

    names = None
    if not args.patterns_only:
        names_path = args.names if args.names.is_absolute() else ROOT / args.names
        if not names_path.is_file():
            print(f"No local name list at {args.names}; run with --patterns-only or create it.")
            return 2
        names = read_names(names_path)
    patterns = read_patterns(ROOT / PATTERNS_FILE)
    files = tracked_files()
    hits = scan(files, names, patterns)
    for hit in hits:
        print(f"{hit.file}:{hit.line}: {hit.source}")
    checked = f"{len(patterns)} patterns" + ("" if names is None else f" and {len(names)} list entries")
    print(f"{checked} checked over {len(files)} tracked files: {len(hits)} hit(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
