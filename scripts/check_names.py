"""
De-identification check (external audit #1, finding 13): no speaker or interviewee
name, and no identifying description, in any tracked file.

    uv run python scripts/check_names.py [work/names.local.txt]

The list lives in a git-ignored local file (one name or phrase per line), drawn
from the owner's local notes, so the names themselves are never committed. The
check is case-insensitive over `git ls-files`; a hit prints the file, the line
number and the list entry's number, never the entry. Exit code 1 on any hit.
"""

import subprocess
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    names_path = Path(argv[0] if argv else "work/names.local.txt")
    names = [line.strip() for line in names_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    listed = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout
    files = listed.decode("utf-8").split(chr(0))
    hits = 0
    for name in filter(None, files):
        path = Path(name)
        if name == names_path.as_posix() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            lowered = line.casefold()
            for index, entry in enumerate(names, start=1):
                if entry.casefold() in lowered:
                    print(f"{name}:{number}: list entry {index}")
                    hits += 1
    print(f"{len(names)} entries checked over {len(files) - 1} tracked files: {hits} hit(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
