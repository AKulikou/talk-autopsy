"""
What the web page offers: target and interface languages, domains, output
profiles, the per-video cost limit, and the owner's remembered choices.

config.py is frozen with `pipeline-v3`, so the offered language list lives
here. The target language stays a parameter end to end; only Russian has been
measured, and the API marks every other language `quality_not_measured`.

The remembered defaults (target language, interface language) are a local,
git-ignored file `web.local.json` in the project root; without it the first
language of each list is the default.
"""

import json
import logging
import os
from pathlib import Path

from talk_autopsy import config

logger = logging.getLogger(__name__)

# (code, name in that language); Lithuanian first, then the rest.
TARGET_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("lt", "Lietuvių"),
    ("ru", "Русский"),
    ("uk", "Українська"),
    ("pl", "Polski"),
    ("lv", "Latviešu"),
    ("de", "Deutsch"),
    ("fr", "Français"),
    ("es", "Español"),
)
MEASURED_LANGUAGES = frozenset({"ru"})  # the only target language with measured quality
UI_LANGUAGES: tuple[tuple[str, str], ...] = (("lt", "Lietuvių"), ("ru", "Русский"), ("en", "English"))
DOMAINS = ("none", "web3")

# Output profiles -> the core's existing --output formats (outputs.FORMATS); no new behaviour.
PROFILES: dict[str, tuple[str, ...]] = {
    "tv": ("srt", "mp4-burned"),
    "laptop": ("srt", "ass", "bilingual", "mp4-soft"),
    "all": ("srt", "ass", "bilingual", "mp4-soft", "mp4-burned"),
}
VIDEO_FORMATS = frozenset({"mp4-soft", "mp4-burned"})

COST_LIMIT_USD = config.COST_CONFIRMATION_USD  # per video, Claude + speech recognition estimate
LOCAL_SETTINGS = config.PROJECT_ROOT / "web.local.json"
STRINGS_DIR = Path(__file__).resolve().parent / "static" / "strings"


def target_codes() -> list[str]:
    return [code for code, _ in TARGET_LANGUAGES]


def language_name(code: str) -> str:
    """The offered target language's own name ("Русский"), or "" for a code not offered."""
    return dict(TARGET_LANGUAGES).get(code, "")


def ui_codes() -> list[str]:
    return [code for code, _ in UI_LANGUAGES]


def load_local(path: Path | None = None) -> dict:
    """The remembered choices; {} if the file is missing or unreadable."""
    path = path or LOCAL_SETTINGS
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        logger.warning("web settings %s not used (%s)", path.name, type(e).__name__)
        return {}


def save_local(updates: dict, path: Path | None = None) -> bool:
    """Merge `updates` into the remembered choices (atomic write); False if it could not be written."""
    path = path or LOCAL_SETTINGS
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        data = {**load_local(path), **updates}
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError as e:
        logger.warning("web settings not saved (%s)", type(e).__name__)
        tmp.unlink(missing_ok=True)
        return False


def default_target_lang(local: dict | None = None) -> str:
    value = (local if local is not None else load_local()).get("target_lang")
    return value if value in target_codes() else target_codes()[0]


def default_ui_lang(local: dict | None = None) -> str:
    value = (local if local is not None else load_local()).get("ui_lang")
    return value if value in ui_codes() else ui_codes()[0]


def ui_strings(lang: str) -> dict[str, str]:
    """The page's strings in `lang` (one of UI_LANGUAGES), English for any key it lacks."""
    if lang not in ui_codes():
        lang = ui_codes()[0]
    english = json.loads((STRINGS_DIR / "en.json").read_text(encoding="utf-8"))
    chosen = json.loads((STRINGS_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    return {**english, **chosen}


def languages_for_page(local: dict | None = None) -> dict:
    local = load_local() if local is None else local
    return {
        "target": [{"code": c, "name": n, "quality_not_measured": c not in MEASURED_LANGUAGES}
                   for c, n in TARGET_LANGUAGES],
        "target_default": default_target_lang(local),
        "ui": [{"code": c, "name": n} for c, n in UI_LANGUAGES],
        "ui_default": default_ui_lang(local),
    }
