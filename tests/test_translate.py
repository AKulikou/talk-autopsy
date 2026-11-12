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


def _echo_ids(kwargs):
    body = kwargs["messages"][0]["content"].split("Segments to translate:")[1]
    lines = re.findall(r"^\[(\d+)\] (.*)$", body, re.M)
    return response(TranslationAnswer(
        segments=[SegmentText(id=int(i), text=f"перевод {i}") for i, _ in lines]))


def test_translation_runs_on_sentence_units(fake_claude):
    fake = fake_claude(_echo_ids)
    t = Transcript(segments=[seg(1, "If you want to try it,"), seg(2, "this is your chance."),
                             seg(3, "Next.")],
                   language="en", source="fixed")
    result = translate.translate(t, GLOSSARY, "ru")
    sent = fake.requests[0]["messages"][0]["content"]
    assert "[1] If you want to try it, this is your chance." in sent and "[2] Next." in sent
    assert result.ids() == [1, 2]
    assert [s.source_ids for s in result.segments] == [[1, 2], [3]]
    assert [w.text for w in result.segments[0].words] == "If you want to try it, this is your chance.".split()
    assert result.segments[0].start == 1.0 and result.segments[0].end == 2.5
    units = result.meta["translation"]["units"]
    assert units["segments"] == 3 and units["units"] == 2 and units["merged_units"] == 1


def test_per_segment_translation_keeps_the_segment_ids(fake_claude):
    fake_claude(_echo_ids)
    t = Transcript(segments=[seg(1, "If you want to try it,"), seg(2, "this is your chance.")],
                   language="en", source="fixed")
    result = translate.translate(t, GLOSSARY, "ru", units=False)
    assert result.ids() == [1, 2] and all(s.words is None and s.source_ids is None for s in result.segments)
    assert result.meta["translation"]["units"] is None


# --- segments with nothing to translate (bug found on a real video, 2026-11-12) -------------------
# A bare version number ("0.8.26.") was taken for a sentence left in English: both attempts were
# rejected and the whole first block (five minutes) stayed in English. A segment whose correct
# translation is the segment itself must pass; an ordinary English sentence must still be rejected.

NOTHING_TO_TRANSLATE = [
    "0.8.26.",                      # the observed segment: a version number
    "42",                           # digits only
    "1, 2, 3...",                   # numbers and punctuation
    "--> = 100%",                   # symbols
    "msg.sender.",                  # member access
    "uint256",                      # letters and digits in one identifier
    "balanceOf()",                  # camelCase call
    "transfer()",                   # a call
    "snake_case_name",              # snake_case
    "Counter.sol",                  # a file name
    "https://example.org/docs/start",  # a URL
    "www.example.org",              # a web address
    "msg.sender, msg.value, tx.origin.",  # several identifiers
    "Claude Code.",                 # a kept glossary term (already passed before the fix)
]


@pytest.mark.parametrize("text", NOTHING_TO_TRANSLATE)
def test_a_segment_with_nothing_to_translate_may_stay_as_it_is(text):
    assert not left_in_english(text, text, GLOSSARY)
    answer = TranslationAnswer(segments=[SegmentText(id=29, text=text)])
    assert validate_block([seg(29, text)], GLOSSARY, "ru", answer) == []


def test_the_observed_block_with_a_version_number_is_accepted():
    sources = [seg(28, "So we're gonna say pragma solidity and then 0.8.0."), seg(29, "0.8.26."),
               seg(30, "Now if you go to the compiler, the latest version is 0.8.26, so we'll use that.")]
    answer = TranslationAnswer(segments=[
        SegmentText(id=28, text="Итак, пишем pragma solidity, а затем 0.8.0."),
        SegmentText(id=29, text="0.8.26."),
        SegmentText(id=30, text="Если открыть компилятор, последняя версия — 0.8.26, её и возьмём.")])
    assert validate_block(sources, GLOSSARY, "ru", answer) == []


@pytest.mark.parametrize("text", [
    "Open the terminal now.",
    "We use version 0.8.26 here.",        # numbers do not excuse English words
    "Open Counter.sol and call balanceOf.",  # identifiers do not excuse English words
    "Then we read msg.sender, msg.value and tx.origin.",
    "Okay.",
    "Step 1 of 3.",
])
def test_an_english_sentence_is_still_rejected(text):
    assert left_in_english(text, text, GLOSSARY)
    answer = TranslationAnswer(segments=[SegmentText(id=1, text=text)])
    assert any("left in English" in p for p in validate_block([seg(1, text)], GLOSSARY, "ru", answer))


@pytest.mark.parametrize("source, target", [
    ("Step 1 2 3 4.", "Шаг 1 2 3 4."),                         # digits are not English words
    ("Version 0.8.26.1 is out.", "Версия 0.8.26.1."),
    ("Then msg.sender, msg.value, tx.origin.", "Затем msg.sender, msg.value, tx.origin."),
])
def test_numbers_and_identifiers_do_not_count_as_english_words(source, target):
    assert not left_in_english(source, target, GLOSSARY)
