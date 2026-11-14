"""
The user's glossary of a run: `--glossary` first (it wins), then a domain
glossary (`glossaries/<domain>.md`) and its local additions
(`glossaries/local/<domain>.md`). Shared by the CLI's `run` and the web page's
jobs, so both build the same file and therefore the same cache keys.
"""

import re
from pathlib import Path

from talk_autopsy import config
from talk_autopsy.glossary import GlossaryError, combine, domain_paths, load_glossary, to_markdown

# A versioned dictionary (`web3-v2.1`): the frozen `glossary.domain_paths` takes names without a dot,
# so a dotted name is resolved here, with the same rule otherwise (one file name, no path).
_VERSIONED = re.compile(r"[a-z0-9][a-z0-9_-]*(\.[0-9]+)+")


def _domain_paths(domain: str) -> list[Path]:
    name = domain.strip().lower()
    if not _VERSIONED.fullmatch(name):
        return domain_paths(domain, config.GLOSSARIES_DIR, config.LOCAL_GLOSSARIES_DIR)
    main = Path(config.GLOSSARIES_DIR) / f"{name}.md"
    if not main.is_file():
        raise GlossaryError(f"no domain glossary {main}")
    local = Path(config.LOCAL_GLOSSARIES_DIR) / f"{name}.md"
    return [main, local] if local.is_file() else [main]


def user_glossary_paths(glossary: Path | None, domain: str | None) -> list[Path]:
    """--glossary first (it wins), then the domain glossary, then its local additions; all parsed now."""
    paths = [glossary] if glossary else []
    if domain:
        paths += _domain_paths(domain)
    for path in paths:
        if not Path(path).is_file():
            raise GlossaryError(f"no glossary file {path}")
        load_glossary(path)
    return paths


def run_glossary(paths: list[Path], work_dir: Path) -> Path | None:
    """The user glossary of a run: one file as given, several combined into work/<id>/user-glossary.md."""
    if not paths:
        return None
    if len(paths) == 1:
        return paths[0]
    combined = combine([load_glossary(p) for p in paths])
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / "user-glossary.md"
    sources = ", ".join(Path(p).name for p in paths)
    out.write_text(f"<!-- user glossary of this run: {sources}, earlier files win -->" + chr(10) * 2
                   + to_markdown(combined), encoding="utf-8", newline=chr(10))
    return out
