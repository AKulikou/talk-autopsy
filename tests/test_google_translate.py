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


def _words_raw(times: list[tuple[float, float, str]]) -> Transcript:
    words = [Word(start=s, end=e, text=t) for s, e, t in times]
    return Transcript(language="en", source="raw.json",
                      segments=[Segment(id=0, start=words[0].start, end=words[-1].end,
                                        text=" ".join(w.text for w in words), words=words)])


def test_words_by_span_assigns_by_midpoint_and_splits_pauses_in_the_middle():
    raw = _words_raw([(0.0, 0.4, " So,"), (0.5, 0.9, "we"),  # before and inside unit 1
                      (2.0, 2.4, "run"),  # pause 1.0-3.0: middle 2.0, word midpoint 2.2 -> unit 2
                      (1.5, 1.9, "it."),  # midpoint 1.7 -> unit 1
                      (3.1, 3.5, "Then"), (9.0, 9.4, "done.")])  # after the last unit -> unit 2
    out = gt.words_by_span(raw, [(1, 0.2, 1.0), (2, 3.0, 4.0)])
    assert out == {1: "So, we it.", 2: "run Then done."}


def test_words_by_span_leaves_an_empty_unit_empty_and_checks_the_order():
    raw = _words_raw([(0.0, 0.4, "one")])
    assert gt.words_by_span(raw, [(1, 0.0, 1.0), (2, 5.0, 6.0)]) == {1: "one", 2: ""}
    with pytest.raises(ValueError, match="time order"):
        gt.words_by_span(raw, [(2, 5.0, 6.0), (1, 0.0, 1.0)])
    with pytest.raises(ValueError, match="overlap"):
        gt.words_by_span(raw, [(1, 0.0, 2.0), (2, 1.0, 3.0)])


def test_script_counts_everything_before_sending(monkeypatch, tmp_path, capsys):
    script = _script()
    monkeypatch.setattr(script.config, "load_environment", lambda *a, **k: False)
    monkeypatch.setattr(script, "WORK", tmp_path)
    (tmp_path / "T2").mkdir()
    save_transcript(_raw(), tmp_path / "T2" / "raw.whisper-large-v3.json")
    monkeypatch.setattr(gt, "http_caller", lambda *a, **k: pytest.fail("no request over the cap"))
    assert script.main(["--fragment", "T2", "--max-chars", "10"]) == 1
    assert "nothing sent" in capsys.readouterr().err
    monkeypatch.setenv(gt.KEY_ENV_VAR, " ")  # count-only needs no key
    assert script.main(["--fragment", "T2", "--count-only"]) == 0
    assert "total 39" in capsys.readouterr().out


def test_script_builds_the_rating_items_from_the_pipeline_units(monkeypatch, tmp_path):
    from talk_autopsy import translation_check, units

    script = _script()
    monkeypatch.setattr(script.config, "load_environment", lambda *a, **k: False)
    monkeypatch.setattr(script, "WORK", tmp_path)
    (tmp_path / "T6").mkdir()
    save_transcript(_raw(), tmp_path / "T6" / "raw.whisper-large-v3.json")  # Whisper = the same words here
    fixed = tmp_path / "fixed.json"
    save_transcript(_raw(), fixed)
    english = units.make_units(_raw())
    sample = tmp_path / "sample.json"
    translation_check.write_json(sample, {"items": [
        {"unit": s.id, "english": s.text.strip(), "translation": "x"} for s in english.segments]})
    log = []
    monkeypatch.setattr(gt, "http_caller", lambda key: fake_caller(log))
    out = tmp_path / "out"
    assert script.main(["--fragment", "T6", "--out", str(out), "--sample", str(sample),
                        "--fixed", str(fixed)]) == 0
    items = json.loads((out / "T6-units.json").read_text(encoding="utf-8"))["items"]
    assert [i["whisper_en"] for i in items] == ["We run the tests first.", "Then we ship it."]
    assert items[0]["translation"] == "[ru] We run the tests first."
    assert len(log) == 2  # one request for the fragment's units, one for the rating items
    wrong = tmp_path / "wrong.json"
    translation_check.write_json(wrong, {"items": [{"unit": english.segments[0].id, "english": "other",
                                                    "translation": "x"}]})
    assert script.main(["--fragment", "T6", "--out", str(out), "--sample", str(wrong),
                        "--fixed", str(fixed)]) == 1
