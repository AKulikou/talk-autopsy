"""The adjudication normaliser (post-hoc, 2026-11-10): repetitions, thousands separators, key combinations."""

import pytest

from talk_autopsy import adjudicate, judge, metrics
from talk_autopsy.judge import (
    canonical_key_combinations,
    collapse_repetitions,
    disagreement_class,
    normalize_for_adjudication,
    remove_thousands_separators,
)


@pytest.mark.parametrize(("text", "expected"), [
    ("For for everyone", "For everyone"),
    ("I wanted to I wanted to include", "I wanted to include"),
    ("in the in the terminal", "in the terminal"),
    ("one two three four one two three four five", "one two three four five"),
    ("the the the end", "the end"),
    ("For, for everyone.", "For, everyone."),
])
def test_collapse_repetitions(text, expected):
    assert collapse_repetitions(text) == expected


def test_collapse_keeps_non_adjacent_repeats():
    assert collapse_repetitions("the cat and the dog") == "the cat and the dog"
    # five words: beyond the limit
    assert collapse_repetitions("a b c d e a b c d e") == "a b c d e a b c d e"


def test_collapse_ignores_case_and_punctuation():
    assert metrics.tokenize(collapse_repetitions("So, so. SO we go")) == ["so", "we", "go"]


def test_collapse_known_limitation_grammatical_doubles():
    assert collapse_repetitions("I know that that works") == "I know that works"


@pytest.mark.parametrize(("text", "expected"), [
    ("a 3,000-line feature", "a 3000-line feature"),
    ("1,000,000 users", "1000000 users"),
    ("version 1,5 and items 2, 3", "version 1,5 and items 2, 3"),
    ("12,34", "12,34"),
])
def test_thousands_separators(text, expected):
    assert remove_thousands_separators(text) == expected


@pytest.mark.parametrize(("text", "expected"), [
    ("shift enter for new lines", "Shift+Enter for new lines"),
    ("Shift-Enter", "Shift+Enter"),
    ("shift+enter", "Shift+Enter"),
    ("Command-F.", "Cmd+F."),
    ("Command+F", "Cmd+F"),
    ("press ctrl - c now", "press Ctrl+C now"),
    ("control shift tab", "Ctrl+Shift+Tab"),
    ("alt+esc", "Alt+Esc"),
    ("Shift Tab", "Shift+Tab"),
    ("command f", "command f"),  # a letter needs an explicit + or -
    ("the command line and control flow", "the command line and control flow"),
])
def test_key_combinations(text, expected):
    assert canonical_key_combinations(text) == expected


def test_normaliser_removes_fillers_then_collapses():
    assert normalize_for_adjudication("for um for everyone") == "for everyone"


@pytest.mark.parametrize("text", [
    "For for everyone that", "I wanted to I wanted to include", "shift enter shift+enter",
    "like, 3,000-line feature. And", "uh the the um the end", "Command-F command-f", "",
])
def test_normaliser_is_idempotent(text):
    once = normalize_for_adjudication(text)
    assert normalize_for_adjudication(once) == once


def test_normaliser_makes_the_09b_convention_cases_agree():
    pairs = [("what we like to see. For everyone that", "what we like to see. For for everyone that"),
             ("I wanted to include", "I wanted to I wanted to include"),
             ("Shift+Enter for new lines.", "shift enter for new lines."),
             ("Command+F.", "Command-F."),
             ("like, 3,000-line feature. And", 'like 3000-line feature." And')]
    for left, right in pairs:
        assert metrics.tokenize(normalize_for_adjudication(left)) == \
            metrics.tokenize(normalize_for_adjudication(right))


def test_repetition_across_a_span_boundary_is_not_seen():
    # 09b span 0104: the "I" of the restart sits in the context words, so the span text
    # "wanted to I wanted to include" holds no immediate repetition (documented limitation)
    assert normalize_for_adjudication("wanted to I wanted to include") == "wanted to I wanted to include"


def test_normaliser_does_not_merge_real_word_differences():
    assert metrics.tokenize(normalize_for_adjudication("there are")) != \
        metrics.tokenize(normalize_for_adjudication("There's"))


def test_metrics_normaliser_unchanged():
    assert metrics.tokenize("For for everyone") == ["for", "for", "everyone"]


@pytest.mark.parametrize(("reference", "judge_text", "expected"), [
    ("Claude Code", "Claude Code basically", "word"),
    ("stand-up", "standup", "formatting"),
    ("at-mention", "@-mention", "formatting"),
    ("use the CLAUDE.md file", "use the cloud.md file", "term"),
])
def test_disagreement_class(reference, judge_text, expected):
    assert disagreement_class(reference, judge_text, ["CLAUDE.md", "Claude Code"]) == expected


def _option(label, text, engines):
    return adjudicate.Option(label=label, engines=engines, text=text, words=len(text.split()))


def test_resolve_postprocess_applies_to_listed_spans_only():
    span = adjudicate.Span(first=1, last=2, verdict="split", number=1,
                           options=[_option("A", "for for everyone", ["gemini"]),
                                    _option("B", "for everyone", ["assemblyai"])])
    consensus = adjudicate.Consensus(
        fragment="TX", engines=["whisper", "assemblyai", "gemini"], audio_seconds=1.0, words=4,
        pieces=[{"pos": 0, "word": "say"}, {"span": 0}, {"pos": 3, "word": "say"}, {"pos": 4, "word": "say"}],
        spans=[span], paragraph_breaks=[])
    choices = {1: adjudicate.Choice(["A"], "")}
    plain = adjudicate.resolve(consensus, choices)
    post = adjudicate.resolve(consensus, choices, postprocess=judge.normalize_for_adjudication)
    assert plain.reference.split() == ["say", "for", "for", "everyone", "say", "say"]
    # only the span's text is normalised: the agreed "say say" stays as it is
    assert post.reference.split() == ["say", "for", "everyone", "say", "say"]
    assert post.protocol["spans"][0]["chosen"] == "A"


def test_rows_added_after_the_calibration_stay_out_of_the_judge_prompt():
    readme = """## Reference conventions

| Case | Rule |
|---|---|
| Numbers | digits |
| Immediate repetitions and restarts | written once (added 2026-11-10 after the judge calibration) |
"""
    assert judge.read_conventions(readme) == ["Numbers: digits."]
