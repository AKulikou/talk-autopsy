"""Translation against the fake Claude client: validator and stage."""

import re

import pytest

from talk_autopsy import translate
from talk_autopsy.glossary import Glossary, GlossaryEntry
from talk_autopsy.segments import Segment, Transcript
from talk_autopsy.translate import SegmentText, TranslationAnswer, left_in_english, validate_block
from tests.conftest import response

GLOSSARY = Glossary(entries=[GlossaryEntry(term="Claude Code", keep=True),
                             GlossaryEntry(term="subagent", keep=False, translations={"ru": "субагент"})])


def seg(i, text):
    return Segment(id=i, start=float(i), end=float(i) + 0.5, text=text)


def test_left_in_english():
    assert left_in_english("Open the terminal now.", "Open the terminal now.", GLOSSARY)
    assert not left_in_english("Open the terminal now.", "Откройте терминал.", GLOSSARY)
    assert not left_in_english("Claude Code.", "Claude Code.", GLOSSARY)  # every word is a kept term


@pytest.mark.parametrize("source, out, message", [
    ("Start a subagent.", "Запустите агента.", "glossary translation"),
    ("Start a subagent.", "Start a subagent.", "left in English"),
    ("Hello.", "", "empty"),
])
def test_validator_rejects(source, out, message):
    problems = validate_block([seg(1, source)], GLOSSARY, "ru",
                              TranslationAnswer(segments=[SegmentText(id=1, text=out)]))
    assert any(message in p for p in problems), problems


def test_validator_accepts_inflected_glossary_translation():
    answer = TranslationAnswer(segments=[SegmentText(id=1, text="Claude Code запускает субагента.")])
    assert validate_block([seg(1, "Claude Code starts a subagent.")], GLOSSARY, "ru", answer) == []


def test_translate_keeps_ids_and_times_and_records_usage(fake_claude):
    def handle(kwargs):
        body = kwargs["messages"][0]["content"].split("Segments to translate:")[1]
        ids = [int(i) for i in re.findall(r"^\[(\d+)\]", body, re.M)]
        return response(TranslationAnswer(segments=[SegmentText(id=i, text=f"сегмент {i}") for i in ids]))
    fake = fake_claude(handle)
    t = Transcript(segments=[seg(1, "One."), seg(2, "Two.")], language="en", source="fixed",
                   meta={"cost_usd": 0.1})
    result = translate.translate(t, GLOSSARY, "RU")
    assert result.language == "ru" and result.ids() == [1, 2]
    assert [s.text for s in result.segments] == ["сегмент 1", "сегмент 2"]
    assert result.segments[1].start == 2.0
    assert result.meta["translation"]["target_lang"] == "ru" and result.meta["input_cost_usd"] == 0.1
    prefix = fake.requests[0]["system"][0]["text"]
    assert "ru (ISO 639 code)" in prefix and "Claude Code (keep as is" in prefix and "субагент" in prefix


def test_target_language_is_a_parameter(fake_claude):
    def handle(kwargs):
        return response(TranslationAnswer(segments=[SegmentText(id=1, text="Eins.")]))
    fake = fake_claude(handle)
    t = Transcript(segments=[seg(1, "One.")], language="en", source="fixed")
    assert translate.translate(t, Glossary(), "de").language == "de"
    assert "de (ISO 639 code)" in fake.requests[0]["system"][0]["text"]


def test_estimate_is_positive():
    t = Transcript(segments=[seg(1, "One two three.")], language="en", source="fixed")
    e = translate.estimate(t, GLOSSARY, "ru", model="claude-sonnet-5-5")
    assert e.blocks == 1 and e.usd > 0


def test_a_dropped_kept_term_is_a_warning_not_a_rejection(fake_claude):
    def handle(kwargs):
        return response(TranslationAnswer(segments=[SegmentText(id=1, text="Запустите его.")]))
    fake_claude(handle)
    t = Transcript(segments=[seg(1, "Run Claude Code.")], language="en", source="fixed")
    answer = TranslationAnswer(segments=[SegmentText(id=1, text="Запустите его.")])
    assert validate_block([seg(1, "Run Claude Code.")], GLOSSARY, "ru", answer) == []
    result = translate.translate(t, GLOSSARY, "ru")
    assert result.segments[0].text == "Запустите его."
    assert result.meta["translation"]["warnings"] == ["segment 1: kept term 'Claude Code' is missing"]
    assert result.meta["translation"]["failed_blocks"] == []
