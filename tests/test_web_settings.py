"""What the web page offers: languages in order, defaults from the local file, profiles."""

import json

from talk_autopsy import outputs
from talk_autopsy.web import settings


def test_lithuanian_first_and_only_russian_measured():
    page = settings.languages_for_page({})
    assert [x["code"] for x in page["target"]][0] == "lt"
    assert [x["code"] for x in page["ui"]][0] == "lt"
    flags = {x["code"]: x["quality_not_measured"] for x in page["target"]}
    assert flags["ru"] is False and all(v for k, v in flags.items() if k != "ru")


def test_defaults_fall_back_to_the_first_language():
    assert settings.default_target_lang({}) == "lt"
    assert settings.default_ui_lang({"ui_lang": "xx"}) == "lt"
    assert settings.default_target_lang({"target_lang": "ru"}) == "ru"


def test_local_file_round_trip(tmp_path):
    path = tmp_path / "web.local.json"
    assert settings.load_local(path) == {}
    assert settings.save_local({"target_lang": "ru"}, path)
    assert settings.save_local({"ui_lang": "en"}, path)
    assert settings.load_local(path) == {"target_lang": "ru", "ui_lang": "en"}
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupted_local_file_is_ignored(tmp_path):
    path = tmp_path / "web.local.json"
    path.write_text("{oops", encoding="utf-8")
    assert settings.load_local(path) == {}
    path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    assert settings.load_local(path) == {}


def test_local_file_is_git_ignored():
    ignore = (settings.LOCAL_SETTINGS.parent / ".gitignore").read_text(encoding="utf-8")
    assert "web.local.json" in ignore.split()


def test_profiles_use_existing_formats_only():
    for formats in settings.PROFILES.values():
        assert set(formats) <= set(outputs.FORMATS)
    assert settings.PROFILES["all"] == outputs.FORMATS
    assert "mp4-burned" not in settings.PROFILES["laptop"]


def test_string_files_have_the_same_keys_and_no_empty_values():
    files = {lang: json.loads((settings.STRINGS_DIR / f"{lang}.json").read_text(encoding="utf-8"))
             for lang in settings.ui_codes()}
    keys = set(files["en"])
    for lang, strings in files.items():
        assert set(strings) == keys, lang
        assert all(isinstance(v, str) and v.strip() for v in strings.values()), lang
    assert settings.ui_strings("ru")["tab_file"] == "Файл на этом компьютере"
    assert settings.ui_strings("xx") == settings.ui_strings("lt")
