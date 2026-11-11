"""Whisper + Google Translate baseline: batching, unit translation, the HTTP caller, the entry script."""

import importlib.util
import io
import json
import urllib.error

import pytest

from talk_autopsy import config
from talk_autopsy.baselines import google_translate as gt
from talk_autopsy.segments import Segment, Transcript, Word, load_transcript, save_transcript


def _segment(seg_id: int, start: float, words: list[str]) -> Segment:
    timed = [Word(start=start + i * 0.4, end=start + i * 0.4 + 0.3, text=w) for i, w in enumerate(words)]
    return Segment(id=seg_id, start=timed[0].start, end=timed[-1].end, text=" ".join(words), words=timed)


def _raw() -> Transcript:
    # Two segments that form one sentence, then a second sentence after a long pause.
    return Transcript(language="en", source="raw.json", segments=[
        _segment(0, 0.0, ["We", "run", "the"]),
        _segment(1, 1.3, ["tests", "first."]),
        _segment(2, 6.0, ["Then", "we", "ship", "it."]),
    ])


def fake_caller(log: list):
    def call(texts, source, target):
        log.append((list(texts), source, target))
        return [f"[{target}] {t}" for t in texts]
    return call


def test_batches_respect_both_limits_and_keep_order():
    assert gt.batches(["aa", "bb", "cc"], max_texts=2, max_chars=100) == [[0, 1], [2]]
    assert gt.batches(["aaaa", "bbbb", "cc"], max_texts=10, max_chars=8) == [[0, 1], [2]]
    assert gt.batches(["x" * 50, "y"], max_texts=10, max_chars=8) == [[0], [1]]  # a long text goes alone
    assert gt.batches([]) == []


def test_translate_units_keeps_ids_times_words_and_counts_characters():
    from talk_autopsy import units
    unit_transcript = units.make_units(_raw())
    log = []
    result = gt.translate_units(unit_transcript, "ru", fake_caller(log))
    assert [s.text for s in result.translated.segments] == [
        "[ru] We run the tests first.", "[ru] Then we ship it."]
    assert [s.source_ids for s in result.translated.segments] == [[0, 1], [2]]
    assert result.translated.segments[0].words == unit_transcript.segments[0].words
    assert result.translated.language == "ru"
    assert result.units == 2 and result.requests == 1
    assert result.characters == len("We run the tests first.") + len("Then we ship it.")
    assert log == [(["We run the tests first.", "Then we ship it."], "en", "ru")]
    assert result.translated.meta["translation"]["baseline"] == gt.VERSION


def test_a_short_answer_is_an_error():
    from talk_autopsy import units
    with pytest.raises(gt.GoogleTranslateError, match="sent 2 texts, got 1"):
        gt.translate_units(units.make_units(_raw()), "ru", lambda texts, s, t: texts[:1])


def test_run_writes_the_translation_and_subtitles(tmp_path):
    raw = save_transcript(_raw(), tmp_path / "raw.json")
    result, srt = gt.run(raw, tmp_path / "out", "T9", "ru", fake_caller([]))
    assert srt == tmp_path / "out" / "T9.ru.srt"
    assert "[ru] We run the tests first." in srt.read_text(encoding="utf-8")
    assert srt.with_suffix(".ass").is_file()
    saved = load_transcript(tmp_path / "out" / "ru.json")
    assert [s.text for s in saved.segments] == [s.text for s in result.translated.segments]


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_http_caller_sends_the_key_in_a_header_and_reads_the_answer():
    seen = {}

    def urlopen(request, timeout):
        seen.update(url=request.full_url, headers=dict(request.header_items()),
                    body=json.loads(request.data.decode("utf-8")), method=request.get_method())
        answer = {"data": {"translations": [{"translatedText": "один"}, {"translatedText": "два"}]}}
        return _Response(json.dumps(answer).encode("utf-8"))

    call = gt.http_caller("secret-key-value", urlopen=urlopen)
    assert call(["one", "two"], "en", "ru") == ["один", "два"]
    assert seen["url"] == gt.ENDPOINT and "secret-key-value" not in seen["url"]
    assert seen["headers"]["X-goog-api-key"] == "secret-key-value"
    assert seen["method"] == "POST"
    assert seen["body"] == {"q": ["one", "two"], "source": "en", "target": "ru", "format": "text"}


def test_http_errors_carry_the_api_message_never_the_key():
    def urlopen(request, timeout):
        body = json.dumps({"error": {"code": 403, "message": "Cloud Translation API has not been used"}})
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(body.encode()))

    with pytest.raises(gt.GoogleTranslateError) as info:
        gt.http_caller("secret-key-value", urlopen=urlopen)(["one"], "en", "ru")
    assert "HTTP 403: Cloud Translation API has not been used" in str(info.value)
    assert "secret-key-value" not in str(info.value)


def test_unusable_answer_is_an_error():
    call = gt.http_caller("k", urlopen=lambda request, timeout: _Response(b'{"data": {}}'))
    with pytest.raises(gt.GoogleTranslateError, match="no data.translations"):
        call(["one"], "en", "ru")


def _script():
    path = config.PROJECT_ROOT / "scripts" / "google_translate_baseline.py"
    spec = importlib.util.spec_from_file_location("google_translate_baseline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_without_a_key_prints_the_setup_steps_and_sends_nothing(monkeypatch, capsys):
    script = _script()
    monkeypatch.setattr(script.config, "load_environment", lambda *a, **k: False)
    monkeypatch.setenv(gt.KEY_ENV_VAR, " ")
    monkeypatch.setattr(gt, "http_caller", lambda *a, **k: pytest.fail("no request without a key"))
    assert script.main([]) == 0
    out = capsys.readouterr().out
    assert "Cloud Translation API" in out and "GOOGLE_TRANSLATE_API_KEY=" in out


def test_script_stops_at_the_character_cap():
    script = _script()
    capped = script._capped(lambda texts, s, t: texts, remaining=10)
    assert capped(["12345"], "en", "ru") == ["12345"]
    with pytest.raises(gt.GoogleTranslateError, match="--max-chars"):
        capped(["123456"], "en", "ru")
