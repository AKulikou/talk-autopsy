"""
Configuration: API key check, target language, models, prices and paths.

Everything a run may vary comes from .env, so an experiment can compare variants
of one code version without editing it. A variable already set in the environment
wins over .env, so CI and servers can pass settings without touching the file.

Importing this module (or any other) reads nothing: .env is loaded only by an
entry point (`load_environment`, called by the CLI's `main`; the web UI will do
the same), and every setting is a function that reads the environment when it
is called. Tests import the package without ever seeing a local .env.

The API key is checked when a Claude stage is about to run (require_api_key),
not at import: cutting audio and speech recognition run without a key.
"""

import os
import re
from pathlib import Path

from dotenv import load_dotenv


def load_environment(dotenv_path: Path | None = None) -> bool:
    """
    Load .env (default: the one in the project root) into the process
    environment; a variable already set in the environment wins. Call it only
    from an entry point, before the first setting is read. True if a file was read.
    """
    return load_dotenv(dotenv_path or PROJECT_ROOT / ".env", override=False)


def _setting(name: str, default: str) -> str:
    """An environment variable, stripped; empty or unset means `default`."""
    return os.getenv(name, "").strip() or default


class ConfigurationError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


# --- Secrets ---------------------------------------------------------------

# Every environment variable that holds a secret: the keys this code reads, and
# the credentials the SDKs it calls would pick up from the environment on their
# own. tests/conftest.py replaces each with a placeholder before every test;
# tests/test_secrets.py fails if the source reads a *_KEY, *_TOKEN or *_SECRET
# variable missing here, or if .env.example and this list differ. Values are
# never printed or logged.
SECRET_ENV_VARS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",     # Claude stages (require_api_key)
    "ANTHROPIC_AUTH_TOKEN",  # read by the anthropic SDK as a bearer token; not used here
    "ASSEMBLYAI_API_KEY",    # AssemblyAI engine (require_key)
    "GEMINI_API_KEY",        # Gemini engine (require_key)
    "GOOGLE_API_KEY",        # read by the google-genai SDK; the explicit GEMINI_API_KEY wins
    "HF_TOKEN",              # read by huggingface_hub when faster-whisper downloads a model
)


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


def key_is_set(name: str) -> bool:
    """True if the secret `name` (one of SECRET_ENV_VARS) is set and not empty; never returns the value."""
    if name not in SECRET_ENV_VARS:
        raise ValueError(f"unknown secret {name!r}; known: {', '.join(SECRET_ENV_VARS)}")
    return bool(os.getenv(name, "").strip())


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


# Gemini speech recognition (stt/gemini.py). Model list checked at
# https://ai.google.dev/gemini-api/docs/models (now served from aistudio.google.com/docs)
# on 2026-11-09: the flagship gemini-4-argon is not on the free tier, so the default
# is the most capable free-tier model; on a quota rejection the engine retries
# once with the fallback. GEMINI_TIER=free records cost 0 (tokens are still
# recorded); paid records the estimate at list prices.
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_GEMINI_FALLBACK_MODEL = "gemini-3.6-flash"


def gemini_model() -> str:
    return _setting("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)


def gemini_fallback_model() -> str:
    return _setting("GEMINI_FALLBACK_MODEL", DEFAULT_GEMINI_FALLBACK_MODEL)


def gemini_tier() -> str:
    return _setting("GEMINI_TIER", "free").lower()


def gemini_models() -> list[str]:
    """
    Model order of the chunked engine (stt/chunked.py): each chunk tries these in turn,
    then faster-whisper. Comma-separated GEMINI_MODELS in .env; default: gemini_model(),
    then gemini_fallback_model().
    """
    listed = [m.strip() for m in os.getenv("GEMINI_MODELS", "").split(",") if m.strip()]
    return list(dict.fromkeys(listed or [gemini_model(), gemini_fallback_model()]))


# Blind audio judge and reference glossary (judge.py, ref_glossary.py; evaluation tooling)
# on the paid tier since 2026-11-10 (decisions). The judge must not be the STT engine's
# model. gemini-3.1-pro-preview answered 404 for this key on 2026-11-10 (absent from its
# model list) and gemini-2.5-pro routes to gemini-3.8-flash from 2026-12-01, so the judge
# is the pricing page's "most capable reasoning model". Thinking cannot be turned off for
# Gemini 4 models; "low" is the lowest level (aistudio.google.com/docs/thinking, 2026-11-10).
DEFAULT_JUDGE_MODEL = "gemini-4-carbon"
DEFAULT_JUDGE_THINKING_LEVEL = "low"
JUDGE_BUDGET_USD = 3.00  # Gemini spend per session by the judge's own counter
JUDGE_ASSUMED_COST_PER_SPAN = 0.01  # the estimate before any span of a run is measured
JUDGE_REESTIMATE_AFTER = 10  # spans; then the projected total is checked against the budget


def judge_model() -> str:
    return _setting("JUDGE_MODEL", DEFAULT_JUDGE_MODEL)


def judge_thinking_level() -> str:
    return _setting("JUDGE_THINKING_LEVEL", DEFAULT_JUDGE_THINKING_LEVEL).lower()


# Gemini paid Standard tier, USD per million tokens: (input, output). Output includes
# thinking tokens; audio input is billed at the input price (no separate audio price is
# listed for these models). Checked on 2026-11-10 at GEMINI_PRICING_SOURCE.
# gemini-4-carbon and gemini-4-argon double to (4.00, 20.00) from 2026-12-01;
# gemini-3.8-flash doubles from 2027-01-01. Update this table before a run after those dates.
GEMINI_PRICING_CHECKED = "2026-11-10"
GEMINI_PRICING_SOURCE = ("https://ai.google.dev/gemini-api/docs/pricing "
                         "(served from aistudio.google.com/docs/pricing)")
GEMINI_PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "gemini-4-carbon": (2.00, 10.00),
    "gemini-4-argon": (2.00, 10.00),
    "gemini-2.5-pro": (1.25, 10.00),  # prompts up to 200k tokens
    "gemini-3.8-flash": (0.75, 3.75),
}


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


def target_lang() -> str:
    """TARGET_LANG from the environment, else the default (not yet checked: see check_lang)."""
    return _setting("TARGET_LANG", DEFAULT_TARGET_LANG)


# --- Models ----------------------------------------------------------------

# Model IDs checked against https://platform.claude.com/docs/en/about-claude/models/overview
# on 2026-11-09. .env overrides each one. Sonnet 5.5 is the project default;
# Opus 5.5 is reserved for experiment B3 (set it through .env for that run only).
DEFAULT_CORRECTION_MODEL = "claude-sonnet-5-5"
DEFAULT_TRANSLATION_MODEL = "claude-sonnet-5-5"
DEFAULT_HELPER_MODEL = "claude-haiku-5-5"



def correction_model() -> str:
    return _setting("CORRECTION_MODEL", DEFAULT_CORRECTION_MODEL)


def translation_model() -> str:
    return _setting("TRANSLATION_MODEL", DEFAULT_TRANSLATION_MODEL)


def helper_model() -> str:
    return _setting("HELPER_MODEL", DEFAULT_HELPER_MODEL)


TRANSLATION_VERSIONS = {"v2": "translation-v2", "v3": "translation-v3"}
DEFAULT_TRANSLATION_VERSION = "translation-v2"  # v3 is an option until the owner's blind rating


def translation_version(value: str | None = None) -> str:
    """
    The translation prompt of `run`: `value` (the CLI's --translation) or
    TRANSLATION_VERSION, as v2 / v3 or the full name; default translation-v2.
    """
    raw = (value or _setting("TRANSLATION_VERSION", DEFAULT_TRANSLATION_VERSION)).strip().lower()
    version = TRANSLATION_VERSIONS.get(raw, raw)
    if version not in TRANSLATION_VERSIONS.values():
        raise ConfigurationError(f"translation version {raw!r}: use v2 or v3")
    return version


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
SEALED_FRAGMENTS = frozenset({"T6"})  # hold-out: never print its span texts
WORK_DIR = PROJECT_ROOT / "work"  # per-run intermediate files (audio.wav, raw.json, ...)
RESULTS_DIR = PROJECT_ROOT / "results"
BENCHMARK_CSV = RESULTS_DIR / "benchmark.csv"
LOGS_DIR = PROJECT_ROOT / "logs"

# --- Outputs, glossaries, YouTube -------------------------------------------

GLOSSARIES_DIR = PROJECT_ROOT / "glossaries"  # reusable domain glossaries (glossaries/<domain>.md)
LOCAL_GLOSSARIES_DIR = GLOSSARIES_DIR / "local"  # personal additions (names), git-ignored


def output_dir() -> Path:
    """OUTPUT_DIR (a relative path is under the project root); default output/ (git-ignored)."""
    path = Path(_setting("OUTPUT_DIR", "output"))
    return path if path.is_absolute() else PROJECT_ROOT / path


# Estimates for a video before it is downloaded (per hour of video), from the
# operational runs of 2026-11-09/10 (docs/experiments.md, Operational runs):
# Claude 0.67-0.82 USD per hour spent (the higher is used), AssemblyAI two passes
# 0.47 USD per hour at list price, wall time 9.1-9.8 min per hour of video
# without download or burn-in (the higher, rounded up, is used).
RUN_CLAUDE_USD_PER_HOUR = 0.82
RUN_STT_USD_PER_HOUR = 0.47
RUN_MINUTES_PER_HOUR = 10.0
PLAYLIST_MAX_COST_USD = 10.00  # default cap for a whole playlist (Claude + speech recognition estimate)
YT_DLP_MAX_AGE_DAYS = 30  # doctor warns when the installed yt-dlp is older (YouTube changes often)
