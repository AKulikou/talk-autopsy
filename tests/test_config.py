"""Configuration checks that need no network: keys, language code, where .env is read."""

import json
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


# --- .env is read only by an entry point -----------------------------------

def test_environment_variable_wins_over_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("TARGET_LANG=de\n", encoding="utf-8")
    monkeypatch.setenv("TARGET_LANG", "uk")
    assert config.load_environment(env_file)
    assert config.target_lang() == "uk"


def test_dotenv_fills_a_variable_the_environment_does_not_set(tmp_path, monkeypatch):
    # The control for the test above: the temporary .env is really read.
    env_file = tmp_path / ".env"
    env_file.write_text("TARGET_LANG=de\n", encoding="utf-8")
    monkeypatch.setenv("TARGET_LANG", "placeholder")  # recorded, so teardown restores the real state
    monkeypatch.delenv("TARGET_LANG")
    config.load_environment(env_file)
    assert config.target_lang() == "de"


def test_settings_are_read_when_called_not_at_import(monkeypatch):
    monkeypatch.setenv("CORRECTION_MODEL", "claude-test-model")
    assert config.correction_model() == "claude-test-model"
    monkeypatch.setenv("CORRECTION_MODEL", " ")
    assert config.correction_model() == config.DEFAULT_CORRECTION_MODEL


# Runs in a fresh interpreter with a .env of sentinel values in its working
# directory. Every way dotenv could load a file is redirected to that file, and
# os.environ records the names read, so the check holds whatever path a stray
# load_dotenv() would use. Prints JSON: what importing every module leaked and
# read, then (the control) what config.load_environment() loads.
_IMPORT_EVERY_MODULE = r"""
import importlib, json, os, pkgutil, sys
from collections.abc import MutableMapping

import dotenv, dotenv.main

sentinel_file, names = sys.argv[1], sys.argv[2].split(",")
_load = dotenv.main.load_dotenv
def _redirected(dotenv_path=None, *args, **kwargs):
    return _load(sentinel_file, *args, **kwargs)
dotenv.load_dotenv = dotenv.main.load_dotenv = _redirected
dotenv.find_dotenv = dotenv.main.find_dotenv = lambda *args, **kwargs: sentinel_file


class Recording(MutableMapping):
    def __init__(self, inner):
        self.inner, self.read = inner, set()
    def __getitem__(self, key):
        self.read.add(key)
        return self.inner[key]
    def __contains__(self, key):
        self.read.add(key)
        return key in self.inner
    def get(self, key, default=None):
        self.read.add(key)
        return self.inner.get(key, default)
    def __setitem__(self, key, value):
        self.inner[key] = value
    def __delitem__(self, key):
        del self.inner[key]
    def __iter__(self):
        return iter(self.inner)
    def __len__(self):
        return len(self.inner)
    def copy(self):
        return dict(self.inner)


os.environ = recording = Recording(os.environ)  # os.getenv reads this global too

import talk_autopsy
modules = [m.name for m in pkgutil.walk_packages(talk_autopsy.__path__, "talk_autopsy.")
           if m.name != "talk_autopsy.__main__"]  # __main__ runs the CLI
for name in modules:
    importlib.import_module(name)
leaked = sorted(n for n in names if os.environ.inner.get(n, "").startswith("sentinel-"))
read = sorted(n for n in names if n in recording.read)

from talk_autopsy import config
config.load_environment()
loaded = sorted(n for n in names if os.environ.inner.get(n, "").startswith("sentinel-"))
print(json.dumps({"modules": modules, "leaked": leaked, "read": read, "loaded": loaded}))
"""

# Secrets and the settings .env commonly holds.
_SENTINEL_NAMES = [*config.SECRET_ENV_VARS, "TARGET_LANG", "CORRECTION_MODEL", "GEMINI_MODELS"]


def test_importing_every_module_reads_neither_dotenv_nor_secrets(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("".join(f"{n}=sentinel-{n}\n" for n in _SENTINEL_NAMES), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in _SENTINEL_NAMES}
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_EVERY_MODULE, str(env_file), ",".join(_SENTINEL_NAMES)],
        env=env, cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert "talk_autopsy.cli" in report["modules"] and "talk_autopsy.stt.gemini" in report["modules"]
    assert report["leaked"] == []
    assert report["read"] == []
    # The control: the harness does see a load when one happens.
    assert report["loaded"] == sorted(_SENTINEL_NAMES)


@pytest.mark.parametrize("name", list(config.OPTIONAL_KEYS))
def test_optional_engine_key_is_required_only_when_asked(monkeypatch, name):
    monkeypatch.setenv(name, "  ")
    with pytest.raises(ConfigurationError, match=f"{name} is not set.*--engine"):
        config.require_key(name)
    monkeypatch.setenv(name, " value ")
    assert config.require_key(name) == "value"


def test_unknown_optional_key_is_a_programming_error():
    with pytest.raises(ValueError, match="unknown key"):
        config.require_key("OTHER_KEY")
