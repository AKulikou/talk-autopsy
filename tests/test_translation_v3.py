"""translation-v3: markers, first occurrence, keep validator; key terms unchanged by classification."""

import re

import pytest

from talk_autopsy import pipeline, translate
from talk_autopsy.glossary import Glossary, GlossaryEntry
from talk_autopsy.glossary_auto import AutoGlossaryAnswer, AutoTerm
from talk_autopsy.segments import Segment, Transcript
from talk_autopsy.term_policy import TermPolicyAnswer
from talk_autopsy.translate import SegmentText, TranslationAnswer
from tests.conftest import response
from tests.test_term_policy import classifier_handler

# --- the key terms of speech recognition do not change --------------------------------


def _pipeline_handler(kwargs):
    if kwargs["output_format"] is AutoGlossaryAnswer:
        terms = [AutoTerm(term="Three", heard_as=[], kind="product", keep=True, translation="", note=""),
                 AutoTerm(term="Two", heard_as=[], kind="term", keep=False, translation="два", note="")]
        return response(AutoGlossaryAnswer(terms=terms))
    if kwargs["output_format"] is TermPolicyAnswer:
        return classifier_handler(kwargs)
    from tests.test_pipeline import claude_handler
    return claude_handler(kwargs)


@pytest.mark.parametrize("version", ["translation-v2", "translation-v3"])
def test_stt_key_terms_are_identical_with_and_without_classification(version, monkeypatch, tmp_path, sine,
                                                                     fake_claude):
    from tests.test_pipeline import FakeAssemblyAI
    calls = []
    monkeypatch.setattr(pipeline, "make_assemblyai", FakeAssemblyAI(calls))
    fake_claude(_pipeline_handler)
    settings = pipeline.RunSettings(source=sine, target_lang="ru", steps=list(pipeline.STEPS),
                                    work_root=tmp_path / version, translation_version=version)
    pipeline.run(settings)
    assert calls[1] == ("audio.flac", True, ["Three", "Two"])
    glossary_md = (settings.work_dir / "glossary.md").read_text(encoding="utf-8")
    assert "policy" not in glossary_md  # the file speech recognition and correction read is not rewritten
    assert (settings.work_dir / "glossary.v3.md").is_file() == (version == "translation-v3")


# --- markers -----------------------------------------------------------------------


def test_first_occurrence_gets_the_brackets_later_ones_do_not():
    texts, warnings = translate.render_markers([
        "Нужны ⟦both:вычислительные мощности|compute⟧ и ⟦ui:Dictation|голосовой ввод⟧.",
        "Больше ⟦both:вычислительных мощностей|Compute⟧, снова ⟦ui:Dictation|голосовой ввод⟧.",
    ])
    assert texts == ["Нужны вычислительные мощности (compute) и Dictation (голосовой ввод).",
                     "Больше вычислительных мощностей, снова Dictation."]
    assert warnings == []


@pytest.mark.parametrize(("text", "expected"), [
    ("Это ⟦both:запуск моделей|inference и дальше.", "Это запуск моделей и дальше."),
    ("Это ⟦both:покрытие⟧ кода.", "Это покрытие кода."),
    ("Кнопка ⟦ui:Run|запуск конец", "Кнопка Run запуск конец"),
    ("Лишняя ⟧ скобка.", "Лишняя скобка."),
])
def test_malformed_markers_are_stripped_and_counted(text, expected):
    texts, warnings = translate.render_markers([text])
    assert texts == [expected] and len(warnings) == 1


def _long_transcript() -> Transcript:
    # 400 s: two translation blocks of 300 s
    segments = [Segment(id=n, start=n * 40.0, end=n * 40.0 + 5, text=f"We need more compute {n}.")
                for n in range(10)]
    return Transcript(segments=segments, language="en", source="fixed")


def _v3_handler(kwargs):
    if kwargs["output_format"] is TermPolicyAnswer:
        return classifier_handler(kwargs)
    body = kwargs["messages"][0]["content"].split("Segments to translate:")[1]
    ids = [int(i) for i in re.findall(r"^\[(\d+)\]", body, re.M)]
    return response(TranslationAnswer(segments=[
        SegmentText(id=i, text=f"Нужно больше ⟦both:вычислительных мощностей|compute⟧ {i}.") for i in ids]))


def test_first_occurrence_across_cues_and_blocks(fake_claude):
    fake = fake_claude(_v3_handler)
    glossary = Glossary(entries=[GlossaryEntry(term="compute", keep=False, translations={"ru": "мощности"},
                                               policy="both")])
    result = translate.translate(_long_transcript(), glossary, "ru", version="translation-v3", units=False)
    texts = [s.text for s in result.segments]
    assert len(fake.requests) == 2  # two blocks
    assert texts[0] == "Нужно больше вычислительных мощностей (compute) 0."
    assert all("(compute)" not in t and "⟦" not in t for t in texts[1:])
    assert result.meta["translation"]["marker_warnings"] == []
    prefix = fake.requests[0]["system"][0]["text"]
    assert "- compute [both] -> мощности" in prefix and "⟦both:" in prefix


def test_v2_output_has_no_marker_processing(fake_claude):
    fake_claude(_v3_handler)
    glossary = Glossary(entries=[GlossaryEntry(term="compute", keep=False)])
    result = translate.translate(_long_transcript(), glossary, "ru", units=False)
    assert "⟦both:" in result.segments[0].text and "marker_warnings" not in result.meta["translation"]


# --- the keep validator ------------------------------------------------------------


KEEP = Glossary(entries=[GlossaryEntry(term="Claude Code", keep=True, policy="keep"),
                         GlossaryEntry(term="call", keep=True, policy="keep"),
                         GlossaryEntry(term="tokens", keep=False, translations={"ru": "токены"},
                                       policy="translate")])


def _check(source: str, out: str) -> list[str]:
    return translate.validate_block_v3([Segment(id=1, start=0, end=1, text=source)], KEEP, "ru",
                                       TranslationAnswer(segments=[SegmentText(id=1, text=out)]))


def test_keep_validator():
    assert _check("Claude Code writes tokens.", "Claude Code пишет токены.") == []
    problems = _check("Claude Code writes.", "Клод Код пишет.")
    assert any("keep term 'Claude Code' is missing" in p for p in problems)
    assert any("missing" in p for p in _check("Claude Code writes.", "Он пишет."))
    # a plain lower-case keep word is an everyday word too: not enforced
    assert _check("We call it a day.", "На сегодня хватит.") == []
    # markers are checked by their visible text
    assert _check("Use Claude Code for inference.",
                  "Используйте Claude Code для ⟦both:запуска моделей|inference⟧.") == []
    assert any("left in English" in p for p in _check("Use the terminal now.", "Use the terminal now."))
