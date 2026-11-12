"""
The static page: every element the scripts look up exists, every string key
they use exists in all three languages, and nothing is loaded from outside
this server (no CDN; the page works offline).
"""

import json
import re

from talk_autopsy.web import settings
from talk_autopsy.web.app import STATIC_DIR, TOKEN_PLACEHOLDER

HTML = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
SCRIPTS = {p.name: p.read_text(encoding="utf-8") for p in STATIC_DIR.glob("*.js")}
STRINGS = json.loads((settings.STRINGS_DIR / "en.json").read_text(encoding="utf-8"))


def test_every_looked_up_id_exists():
    ids = set(re.findall(r'\bid="([^"]+)"', HTML))
    used = set()
    for source in SCRIPTS.values():
        used |= set(re.findall(r'\$\("([^"]+)"\)', source))
    assert used and used <= ids, sorted(used - ids)


def test_every_static_string_key_exists():
    keys = set(re.findall(r'data-i18n(?:-placeholder)?="([^"]+)"', HTML))
    for source in SCRIPTS.values():
        keys |= set(re.findall(r'\bt\("([a-z0-9_]+)"', source))
    assert keys <= set(STRINGS), sorted(keys - set(STRINGS))


def test_dynamic_string_keys_exist():
    steps = ["download", "stt", "glossary", "stt2", "align", "fix", "translate", "srt", "outputs"]
    states = ("waiting", "running", "done", "skipped", "failed")
    purposes = ("tv", "subtitles", "subtitles_styled", "bilingual", "video_tracks")
    hints = ("doctor", "youtube", "outputs", "cost_limit", "input", "log")
    dynamic = ([f"step_{s}" for s in steps] + [f"state_{s}" for s in states]
               + [f"purpose_{p}" for p in purposes] + [f"hint_{h}" for h in hints]
               + [f"doctor_{d}" for d in ("ok", "missing", "warning")] + ["unit_block", "unit_chunk"])
    assert set(dynamic) <= set(STRINGS), sorted(set(dynamic) - set(STRINGS))


def test_error_codes_of_the_server_have_strings():
    codes = {"neither_given", "both_given", "bad_url", "playlist", "not_absolute", "no_such_file",
             "not_a_file", "bad_extension", "bad_path", "unreadable_file", "bad_language", "youtube", "busy",
             "confirm_cost", "cost_mismatch", "confirm_no_estimate", "bad_token", "network", "outside_output",
             "internal"}
    assert {f"err_{c}" for c in codes} <= set(STRINGS)


def test_nothing_is_loaded_from_outside():
    for text in [HTML, *SCRIPTS.values(), (STATIC_DIR / "app.css").read_text(encoding="utf-8")]:
        assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", text)
        assert "@import" not in text and "cdn" not in text.lower()


def test_the_page_has_the_token_placeholder_and_language_tabs():
    assert TOKEN_PLACEHOLDER in HTML
    assert 'role="tablist"' in HTML and HTML.count('role="tab"') == 2
    assert 'aria-selected="true"' in HTML.split('data-mode="url"')[0].rsplit("<button", 1)[1]
