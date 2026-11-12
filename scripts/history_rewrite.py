"""
Git-history rewrite helper: replace identifying details in every commit's files
and in every commit and tag message, then check the result commit by commit.
Nothing here names anyone: the names come from the git-ignored local list
(`work/names.local.txt`), the generic descriptors from the committed
`scripts/identifying_patterns.txt` (the same two sources as
`scripts/check_names.py`).

    # 1. the rules (they hold the names: written under work/, git-ignored, never printed)
    uv run python scripts/history_rewrite.py rules --out work/rewrite-rules.local.json
    # 2. the rewrite, in a fresh clone only (git-filter-repo's own Python: uv tool install git-filter-repo)
    uvx --from git-filter-repo python scripts/history_rewrite.py rewrite --repo <clone> \\
        --rules work/rewrite-rules.local.json
    # 3. every commit's tree, every path, every commit and tag message (counts and places, never the text)
    uv run python scripts/history_rewrite.py verify --repo <clone>
    # 4. commit hashes cited in the docs: old -> new (needs the clone's filter-repo commit map)
    uv run python scripts/history_rewrite.py hashmap --old . --new <clone>
    uv run python scripts/history_rewrite.py hashmap --old . --new <clone> --apply   # then commit

What is replaced (file contents, file paths, commit and tag messages):

- each entry of the local list, as whole words across line breaks (as the
  check matches it), by `[removed]`; the "## Sources" section of
  `data/test-set/README.md`, where the published recordings' speakers are
  named on purpose, is left as it is;
- a nationality or region word before "accent" by "regional accent", and after
  "accent:" / "accent (" by "regional";
- the job-title phrase (and its Russian calque) by "staff member" ("сотрудник");
- a role at a named company by the role alone ("<company> engineer" ->
  "engineer");
- an original file name that looks like a video ID by `source-video-<n>.<ext>`,
  the same number for the same ID everywhere.

The three files that hold the patterns and their test examples
(`check_names.EXEMPT`) are not rewritten, as the check does not scan them:
they must contain the generic descriptors to work.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import check_names  # noqa: E402  (stdlib only; shared rules with the check)

REMOVED = "[removed]"
HASH = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{7,40}(?![0-9A-Za-z])")
CITED = ["README.md", "CHANGELOG.md", "docs", "results"]


# --- rules ------------------------------------------------------------------------------


def build_rules(names_path: Path, patterns_path: Path) -> dict:
    names = check_names.read_names(names_path)
    patterns = [p.pattern for p in check_names.read_patterns(patterns_path)]
    if len(patterns) != 7:
        raise SystemExit(f"expected the 7 patterns of identifying_patterns.txt, found {len(patterns)}; "
                         "update the replacement for each pattern in history_rewrite.py first")
    return {"names": sorted(names, key=len, reverse=True), "patterns": patterns, "removed": REMOVED,
            "exempt": sorted(check_names.EXEMPT),
            "names_allowed_sections": check_names.NAMES_ALLOWED_SECTIONS}


class Replacer:
    """Text replacements built from the rules; counts what it replaced by kind (never the text)."""

    def __init__(self, rules: dict):
        self.names = [check_names.name_regex(n) for n in rules["names"]]
        self.patterns = [re.compile(p, re.IGNORECASE) for p in rules["patterns"]]
        self.removed = rules["removed"]
        self.allowed = rules["names_allowed_sections"]
        self.video_ids: dict[str, int] = {}
        self.counts: Counter = Counter()

    @staticmethod
    def _like(original: str, new: str) -> str:
        return new[:1].upper() + new[1:] if original[:1].isupper() else new

    def _video(self, m: re.Match) -> str:
        stem, ext = m.group(0).rsplit(".", 1)
        number = self.video_ids.setdefault(stem, len(self.video_ids) + 1)
        return f"source-video-{number}.{ext}"

    def patterns_only(self, text: str) -> str:
        subs = [
            lambda m: "regional accent",  # 1 (a nationality word is capitalised anyway)
            lambda m: re.sub(r"([:(]\s*).*$", r"\1regional", m.group(0), flags=re.S),  # 2
            lambda m: self._like(m.group(0), "staff member"),                        # 3
            lambda m: "сотрудник",                                                   # 4
            lambda m: m.group(0).split()[-1],                                        # 5: "<company> engineer"
            lambda m: m.group(0).split()[0],                                         # 6: "<role> at <co>"
            self._video,                                                             # 7
        ]
        for i, (regex, sub) in enumerate(zip(self.patterns, subs, strict=True), start=1):
            text, n = regex.subn(sub, text)
            self.counts[f"pattern {i}"] += n
        # articles in front of what was replaced: "an <nationality> accent", "a <company> engineer"
        text = re.sub(r"\b([Aa])n regional accent", r"\1 regional accent", text)
        text = re.sub(r"\b([Aa]) (engineers?|employees?)\b", r"\1n \2", text)
        return text

    def names_only(self, text: str) -> str:
        for regex in self.names:
            text, n = regex.subn(self.removed, text)
            self.counts["list entries"] += n
        return text

    def text(self, text: str, path: str | None = None) -> str:
        text = self.patterns_only(text)
        heading = self.allowed.get(path) if path else None
        if heading is None:
            return self.names_only(text)
        # the allowed section keeps its names; everything around it is replaced
        lines = text.split("\n")
        level = len(heading) - len(heading.lstrip("#"))
        out, inside, chunk = [], False, []

        def flush():
            if chunk:
                block = "\n".join(chunk)
                out.append(block if inside else self.names_only(block))
                chunk.clear()

        for line in lines:
            hashes = len(line) - len(line.lstrip("#"))
            starts = line.strip() == heading
            ends = inside and hashes and hashes <= level and line[hashes:hashes + 1] == " "
            if starts or ends:
                flush()
                inside = starts
            chunk.append(line)
        flush()
        return "\n".join(out)


# --- rewrite (git-filter-repo) ----------------------------------------------------------


def rewrite(repo: Path, rules: dict) -> None:
    try:
        import git_filter_repo as fr
    except ImportError:
        raise SystemExit("run this step with git-filter-repo's Python: "
                         "uvx --from git-filter-repo python ...") from None
    replacer = Replacer(rules)
    exempt = {e.encode() for e in rules["exempt"]}
    seen: dict[bytes, bytes] = {}

    def file_info(filename: bytes, mode: bytes, blob_id: bytes, value):
        path = filename.decode("utf-8", "surrogateescape")
        new_name = replacer.text(path).encode("utf-8", "surrogateescape")
        if filename in exempt:
            return new_name, mode, blob_id
        key = filename + b"\0" + blob_id
        if key not in seen:
            contents = value.get_contents_by_identifier(blob_id)
            new_id = blob_id
            if contents is not None and not value.is_binary(contents):
                try:
                    text = contents.decode("utf-8")
                except UnicodeDecodeError:
                    text = None
                if text is not None:
                    new_text = replacer.text(text, path)
                    if new_text != text:
                        new_id = value.insert_file_with_contents(new_text.encode("utf-8"))
            seen[key] = new_id
        return new_name, mode, seen[key]

    def message(msg: bytes) -> bytes:
        return replacer.text(msg.decode("utf-8", "surrogateescape")).encode("utf-8", "surrogateescape")

    # no --force: git-filter-repo itself refuses to run anywhere but in a fresh clone
    args = fr.FilteringOptions.parse_args([], error_on_empty=False)
    previous = os.getcwd()
    os.chdir(repo)
    try:
        fr.RepoFilter(args, file_info_callback=file_info, message_callback=message).run()
    finally:
        os.chdir(previous)
    counts = dict(replacer.counts)
    counts["video ids"] = len(replacer.video_ids)
    print("replacements:", json.dumps(counts, sort_keys=True))


# --- verify -----------------------------------------------------------------------------


class Objects:
    """`git cat-file --batch` on a repository: blob and message text by object id."""

    def __init__(self, repo: Path):
        self.proc = subprocess.Popen(["git", "cat-file", "--batch"], cwd=repo, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE)

    def read(self, oid: str) -> bytes:
        self.proc.stdin.write(oid.encode() + b"\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().split()
        size = int(header[2])
        data = self.proc.stdout.read(size + 1)[:-1]
        return data


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True,
                          encoding="utf-8", errors="surrogateescape").stdout


def verify(repo: Path, names_path: Path, patterns_path: Path) -> int:
    names = check_names.read_names(names_path)
    name_regexes = [(f"list entry {i}", check_names.name_regex(n)) for i, n in enumerate(names, start=1)]
    pattern_regexes = [(f"pattern {i}", p) for i, p in enumerate(check_names.read_patterns(patterns_path), 1)]
    objects = Objects(repo)
    blob_hits: dict[tuple[str, str], list] = {}
    commits = git(repo, "rev-list", "--all").split()
    problems: Counter = Counter()
    first_seen: dict[str, str] = {}
    for commit in commits:
        for entry in git(repo, "ls-tree", "-r", "-z", commit).split("\0"):
            if not entry:
                continue
            meta, path = entry.split("\t", 1)
            _, kind, oid = meta.split()
            shown = path  # a path is printed only with every match masked
            for _, regex in name_regexes + pattern_regexes:
                shown = regex.sub("<...>", shown)
            for hit in check_names.find_hits(shown, path, name_regexes + pattern_regexes):
                problems[f"path {shown}: {hit.source}"] += 1
            if kind != "blob" or path in check_names.EXEMPT:
                continue
            key = (path, oid)
            if key not in blob_hits:
                try:
                    text = objects.read(oid).decode("utf-8")
                except UnicodeDecodeError:
                    blob_hits[key] = []
                    continue
                names_text = text
                if path in check_names.NAMES_ALLOWED_SECTIONS:
                    names_text = check_names.blank_section(text, check_names.NAMES_ALLOWED_SECTIONS[path])
                blob_hits[key] = (check_names.find_hits(path, names_text, name_regexes)
                                  + check_names.find_hits(path, text, pattern_regexes))
            for hit in blob_hits[key]:
                label = f"{shown}:{hit.line}: {hit.source}"
                first_seen.setdefault(label, commit[:10])
                problems[label] += 1
        msg = objects.read(commit).decode("utf-8", "replace").split("\n\n", 1)[-1]
        for hit in check_names.find_hits(f"message of {commit[:10]}", msg, name_regexes + pattern_regexes):
            problems[f"{hit.file}: {hit.source}"] += 1
    tags = [t for t in git(repo, "for-each-ref", "--format=%(objectname) %(objecttype) %(refname:short)",
                           "refs/tags").splitlines() if t]
    for line in tags:
        oid, kind, name = line.split(" ", 2)
        if kind == "tag":
            msg = objects.read(oid).decode("utf-8", "replace").split("\n\n", 1)[-1]
            for hit in check_names.find_hits(f"tag {name}", msg, name_regexes + pattern_regexes):
                problems[f"{hit.file}: {hit.source}"] += 1
    for label, count in sorted(problems.items()):
        where = f" (first in {first_seen[label]})" if label in first_seen else ""
        print(f"{label} -- in {count} commit(s){where}")
    print(f"{len(names)} list entries and {len(pattern_regexes)} patterns checked over {len(commits)} "
          "commits, "
          f"{len(blob_hits)} distinct file versions, {len(commits)} commit messages and "
          f"{sum(1 for t in tags if ' tag ' in t)} tag messages: {len(problems)} distinct hit(s)")
    return 1 if problems else 0


# --- cited hashes -----------------------------------------------------------------------


def hashmap(old: Path, new: Path, apply: bool = False) -> None:
    """
    The table of cited hashes. With `apply`, the new repository's working files get the new
    hashes, line by line (the files must still equal the old ones: the tip's tree is unchanged
    by the rewrite); commit that as its own change.
    """
    pairs = (new / ".git" / "filter-repo" / "commit-map").read_text(encoding="utf-8").split("\n")[1:]
    mapping = dict(p.split() for p in pairs if p.strip())
    full = git(old, "rev-list", "--all").split()
    files = [f for f in git(old, "ls-files", "-z", *CITED).split("\0") if f]
    rows, unmapped = [], 0
    for name in files:
        try:
            text = (old / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for m in HASH.finditer(line):
                short = m.group(0)
                matches = [c for c in full if c.startswith(short)]
                if len(matches) != 1:
                    continue  # not a commit of this repository (or ambiguous)
                target = mapping.get(matches[0])
                if target is None:
                    unmapped += 1
                    rows.append((name, number, short, "(not in the map)"))
                    continue
                rows.append((name, number, short, target[:len(short)]))
    print("| file | line | old | new |")
    print("|---|---|---|---|")
    for name, number, short, target in rows:
        print(f"| {name} | {number} | {short} | {target} |")
    changed = sum(1 for r in rows if r[2] != r[3])
    print(f"\n{len(rows)} cited hash(es) in {len({r[0] for r in rows})} file(s); {changed} change; "
          f"{unmapped} not in the map")
    if not apply:
        return
    by_file: dict[str, list] = {}
    for name, number, short, target in rows:
        if target != short and not target.startswith("("):
            by_file.setdefault(name, []).append((number, short, target))
    for name, edits in by_file.items():
        path = new / name
        crlf = b"\r\n", b"\n"
        if path.read_bytes().replace(*crlf) != (old / name).read_bytes().replace(*crlf):
            raise SystemExit(f"{name} differs between the old and the new repository; not applied")
        lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
        for number, short, target in edits:
            hit = re.compile(rf"(?<![0-9A-Za-z]){short}(?![0-9A-Za-z])")
            lines[number - 1] = hit.sub(target, lines[number - 1])
        path.write_bytes("".join(lines).encode("utf-8"))
    print(f"applied in {len(by_file)} file(s) of {new}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("rules", help="write the rules file (it holds the names: keep it under work/)")
    p.add_argument("--names", type=Path, default=check_names.ROOT / check_names.DEFAULT_NAMES)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("rewrite", help="rewrite a fresh clone in place (git-filter-repo's Python)")
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--rules", type=Path, required=True)
    p = sub.add_parser("verify", help="check every commit, path and message of a repository")
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--names", type=Path, default=check_names.ROOT / check_names.DEFAULT_NAMES)
    p = sub.add_parser("hashmap", help="commit hashes cited in the docs, old -> new")
    p.add_argument("--old", type=Path, required=True)
    p.add_argument("--new", type=Path, required=True)
    p.add_argument("--apply", action="store_true",
                   help="write the new hashes into the new repository's files")
    args = parser.parse_args(argv)
    patterns = check_names.ROOT / check_names.PATTERNS_FILE
    if args.command == "rules":
        rules = build_rules(args.names, patterns)
        args.out.write_text(json.dumps(rules, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {args.out}: {len(rules['names'])} list entries, {len(rules['patterns'])} patterns "
              "(the file holds the names: never commit or print it)")
        return 0
    if args.command == "rewrite":
        rewrite(args.repo.resolve(), json.loads(args.rules.read_text(encoding="utf-8")))
        return 0
    if args.command == "verify":
        return verify(args.repo.resolve(), args.names, patterns)
    hashmap(args.old.resolve(), args.new.resolve(), apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
