"""Configuration checks that need no network: API key and language code."""

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
