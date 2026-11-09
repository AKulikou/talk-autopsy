"""Configuration checks that need no network: API key and language code."""

import os
import subprocess
import sys

import pytest

from talk_autopsy import config
from talk_autopsy.config import ConfigurationError, check_lang, require_api_key


@pytest.mark.parametrize("key", ["", "   ", "not-a-key", "sk-other-123"])
def test_missing_or_malformed_key_fails_with_a_hint(monkeypatch, key):
    monkeypatch.setenv(config.API_KEY_ENV_VAR, key)
    with pytest.raises(ConfigurationError, match=".env.example"):
        require_api_key()


def test_plausible_key_is_returned(monkeypatch):
    monkeypatch.setenv(config.API_KEY_ENV_VAR, "sk-ant-abc")
    assert require_api_key() == "sk-ant-abc"


@pytest.mark.parametrize("code, expected", [("ru", "ru"), (" DE ", "de"), ("ukr", "ukr")])
def test_language_codes_are_normalized(code, expected):
    assert check_lang(code) == expected


@pytest.mark.parametrize("code", ["", "russian", "r", "ru-RU", "12"])
def test_anything_but_a_language_code_is_rejected(code):
    with pytest.raises(ConfigurationError):
        check_lang(code)


def test_every_default_model_has_a_price():
    for model in (
        config.DEFAULT_CORRECTION_MODEL,
        config.DEFAULT_TRANSLATION_MODEL,
        config.DEFAULT_HELPER_MODEL,
    ):
        assert model in config.MODEL_PRICING_PER_MTOK


# config.py reads .env at import, so the precedence check runs in a fresh
# interpreter. find_dotenv is pointed at a temporary file; the project's own .env
# is never read or touched.
_IMPORT_WITH_DOTENV = """
import sys
import dotenv.main
dotenv.main.find_dotenv = lambda *args, **kwargs: sys.argv[1]
from talk_autopsy import config
print(config.TARGET_LANG)
"""


def _target_lang_after_import(tmp_path, environment_value):
    env_file = tmp_path / ".env"
    env_file.write_text("TARGET_LANG=de\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "TARGET_LANG"}
    if environment_value is not None:
        env["TARGET_LANG"] = environment_value
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_WITH_DOTENV, str(env_file)],
        env=env, cwd=config.PROJECT_ROOT, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def test_environment_variable_wins_over_dotenv(tmp_path):
    assert _target_lang_after_import(tmp_path, "uk") == "uk"


def test_dotenv_fills_a_variable_the_environment_does_not_set(tmp_path):
    # The control for the test above: the temporary .env is really read.
    assert _target_lang_after_import(tmp_path, None) == "de"
