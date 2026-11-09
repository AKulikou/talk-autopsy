"""
Configuration: API key check, target language, models, prices and paths.

Everything a run may vary comes from .env, so an experiment can compare variants
of one code version without editing it. A variable already set in the environment
wins over .env, so CI and servers can pass settings without touching the file.

The API key is checked when a Claude stage is about to run (require_api_key),
not at import: cutting audio and speech recognition run without a key.
"""

import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # override=False: the real environment wins


class ConfigurationError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


# --- API key ---------------------------------------------------------------

API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"
API_KEY_PREFIX = "sk-ant-"


def require_api_key() -> str:
    """
    The API key, or ConfigurationError if it is missing or malformed.

    Called before the first Claude call of a run (and by the CLI before any work
    on a run that includes a Claude stage), so a missing key fails in the first
    second instead of after minutes of speech recognition.
    """
    key = os.getenv(API_KEY_ENV_VAR, "").strip()
    if not key.startswith(API_KEY_PREFIX):
        raise ConfigurationError(
            f"{API_KEY_ENV_VAR} is missing or invalid "
            f"(expected a key starting with '{API_KEY_PREFIX}').\n"
            "Copy .env.example to .env in the project root and set your API key there."
        )
    return key


# --- Optional keys of the cloud speech recognition engines ------------------

# Each is needed only when its engine is selected (`transcribe --engine`), so
# they are read when the engine starts, never at import. The values are never
# printed or logged.
OPTIONAL_KEYS: dict[str, str] = {
    "ASSEMBLYAI_API_KEY": "the AssemblyAI engine (transcribe --engine assemblyai)",
    "GEMINI_API_KEY": "the Gemini engine (transcribe --engine gemini)",
}


def require_key(name: str) -> str:
    """
    The value of the optional key `name` (one of OPTIONAL_KEYS), or
    ConfigurationError naming the engine that needs it. Call it only when that
    engine is selected.
    """
    if name not in OPTIONAL_KEYS:
        raise ValueError(f"unknown key {name!r}; known: {', '.join(OPTIONAL_KEYS)}")
    key = os.getenv(name, "").strip()
    if not key:
        raise ConfigurationError(
            f"{name} is not set; it is needed by {OPTIONAL_KEYS[name]}.\n"
            "Set it in .env in the project root (see .env.example)."
        )
    return key


# --- Target language -------------------------------------------------------

# ISO 639-1 (or 639-3) code. The core never assumes a language: every stage takes
# target_lang as an argument. This default is configuration only; Russian is the
# only language measured and accepted so far.
DEFAULT_TARGET_LANG = "ru"
LANG_CODE = re.compile(r"^[a-z]{2,3}$")


def check_lang(code: str) -> str:
    """A normalized language code, or ConfigurationError for anything else."""
    normalized = code.strip().lower()
    if not LANG_CODE.match(normalized):
        raise ConfigurationError(f"Language must be an ISO 639 code such as 'ru' or 'de', got {code!r}")
    return normalized


TARGET_LANG = os.getenv("TARGET_LANG", "").strip() or DEFAULT_TARGET_LANG

# --- Models ----------------------------------------------------------------

# Model IDs checked against https://platform.claude.com/docs/en/about-claude/models/overview
# on 2026-11-09. .env overrides each one. Sonnet 5.5 is the project default;
# Opus 5.5 is reserved for experiment B3 (set it through .env for that run only).
DEFAULT_CORRECTION_MODEL = "claude-sonnet-5-5"
DEFAULT_TRANSLATION_MODEL = "claude-sonnet-5-5"
DEFAULT_HELPER_MODEL = "claude-haiku-5-5"

CORRECTION_MODEL = os.getenv("CORRECTION_MODEL", "").strip() or DEFAULT_CORRECTION_MODEL
TRANSLATION_MODEL = os.getenv("TRANSLATION_MODEL", "").strip() or DEFAULT_TRANSLATION_MODEL
HELPER_MODEL = os.getenv("HELPER_MODEL", "").strip() or DEFAULT_HELPER_MODEL

# Per-call output limit; a truncated response is treated as an error.
MAX_TOKENS = 16000

# --- Prices (cost estimate only) -------------------------------------------

# USD per million tokens: (input, output). Checked against
# https://platform.claude.com/docs/en/about-claude/pricing on 2026-11-09.
# Claude Haiku 5.5 costs more for prompts over 100,000 tokens ($0.50 / $2.50);
# the estimate uses the lower tier, since a transcript block stays far below it.
MODEL_PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Prompt caching prices, as multipliers of the model's input price (pricing page,
# checked 2026-11-09). A 5-minute cache write costs 1.25x; a read costs 0.1x,
# except on the models listed in CACHE_READ_MULTIPLIER_BY_MODEL.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10
CACHE_READ_MULTIPLIER_BY_MODEL: dict[str, float] = {
    "claude-opus-5-5": 0.05,
    "claude-sonnet-5-5": 0.05,
}

# Web search is billed per search on top of tokens. The pipeline does not use it;
# the shared cost code still counts it.
WEB_SEARCH_PRICE = 0.01

# A full-video run whose estimate is above this needs the owner's confirmation.
COST_CONFIRMATION_USD = 3.00

# --- Paths -----------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_SET_DIR = PROJECT_ROOT / "data" / "test-set"
WORK_DIR = PROJECT_ROOT / "work"  # per-run intermediate files (audio.wav, raw.json, ...)
RESULTS_DIR = PROJECT_ROOT / "results"
BENCHMARK_CSV = RESULTS_DIR / "benchmark.csv"
LOGS_DIR = PROJECT_ROOT / "logs"
