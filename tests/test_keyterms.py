"""AssemblyAI key terms within the service limit: 1,000 words, user terms first (review #1, finding 14)."""

import logging

from talk_autopsy.glossary import Glossary, GlossaryEntry
from talk_autopsy.stt import assemblyai
from talk_autopsy.stt.assemblyai import select_keyterms


def entry(term, notes=""):
    return GlossaryEntry(term=term, keep=True, notes=notes)


def test_everything_fits_in_glossary_order():
    glossary = Glossary(entries=[entry("Claude Code"), entry("npm")])
    assert assemblyai.keyterms(glossary) == ["Claude Code", "npm"]


def test_over_the_word_limit_user_terms_first_then_by_frequency_and_no_error(caplog):
    glossary = Glossary(entries=[entry("rare tool"), entry("Kafka"), entry("Redis", "tool; heard as: red is"),
                                 entry("mine one")])
    transcript = "Kafka and red is and red is and Redis and Kafka"
    with caplog.at_level(logging.WARNING):
        selection = select_keyterms(glossary, user_terms=["mine one"], transcript=transcript, max_words=4)
    # user term (2 words), then Redis (3 by its heard-as form), Kafka (2), rare tool (0)
    assert selection.terms == ["mine one", "Redis", "Kafka"]
    assert selection.dropped == ["rare tool"] and selection.words == 4
    assert "dropped 1 term(s): rare tool" in caplog.text


def test_a_shorter_term_still_fits_after_a_longer_one_is_dropped():
    glossary = Glossary(entries=[entry("a b c"), entry("d")])
    selection = select_keyterms(glossary, transcript="a b c a b c d", max_words=2)
    assert selection.terms == ["d"] and selection.dropped == ["a b c"]


def test_terms_over_six_words_are_left_out_not_cut():
    glossary = Glossary(entries=[entry("one two three four five six seven"), entry("Docker")])
    selection = select_keyterms(glossary)
    assert selection.terms == ["Docker"] and selection.too_long == ["one two three four five six seven"]


def test_the_default_limit_is_a_thousand_words():
    glossary = Glossary(entries=[entry(f"term{n} word") for n in range(600)])
    selection = select_keyterms(glossary)
    assert selection.words == 1000 and len(selection.terms) == 500 and len(selection.dropped) == 100
