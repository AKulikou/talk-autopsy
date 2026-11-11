"""
Term accuracy v2 (position-aware, possessives, precision, macro, without the top
term), the Wilson interval and the T6 verdict. The first tests are the external
audit's probes (2026-11-11), kept as regression tests.
"""

import pytest

from talk_autopsy import metrics
from talk_autopsy.metrics import term_accuracy_v1, term_accuracy_v2

# --- the audit's probes ----------------------------------------------------------------


def test_a_term_missed_in_place_but_written_elsewhere_is_not_a_hit():
    reference = "first we start Docker and then we talk about the weather"
    hypothesis = "first we start doctor and then we talk about Docker weather"
    assert term_accuracy_v1(reference, hypothesis, ["Docker"]).accuracy == 1.0  # the old leniency
    result = term_accuracy_v2(reference, hypothesis, ["Docker"])
    assert result.accuracy == 0.0
    assert result.false_positives == 1 and result.precision == 0.0


def test_six_spurious_terms_lower_precision_not_accuracy():
    reference = "we deploy with Kubernetes today"
    hypothesis = "we deploy with Kubernetes today " + " ".join(["Kubernetes"] * 6)
    result = term_accuracy_v2(reference, hypothesis, ["Kubernetes"])
    assert result.accuracy == 1.0
    assert (result.false_positives, result.hypothesis_occurrences) == (6, 7)
    assert result.precision == pytest.approx(1 / 7)


def test_a_term_absent_from_the_reference_still_counts_as_a_false_positive():
    result = term_accuracy_v2("we use Docker", "we use Docker with Terraform", ["Docker", "Terraform"])
    assert result.absent == ["Terraform"] and result.accuracy == 1.0
    assert result.false_positives == 1 and result.precision == pytest.approx(1 / 2)


def test_possessive_matches_the_term():
    reference = "Anthropic's new model and GitHub's runners"
    result = term_accuracy_v2(reference, reference, ["Anthropic", "GitHub"])
    assert [(r.term, r.expected, r.found) for r in result.rows] == [("Anthropic", 1, 1), ("GitHub", 1, 1)]
    assert term_accuracy_v1(reference, reference, ["Anthropic"]).absent == ["Anthropic"]  # v1 missed it


def test_possessive_keeps_the_case_rule_and_accepts_typographic_apostrophes():
    result = term_accuracy_v2("GitHub’s API", "Github's API", ["GitHub"])
    assert result.rows[0].expected == 1 and result.rows[0].found == 0  # identifier: case matters


# --- position, alignment ---------------------------------------------------------------


def test_extra_words_around_the_term_do_not_matter():
    reference = "open Claude Code and run it"
    hypothesis = "so open uh the Claude Code and run it now"
    assert term_accuracy_v2(reference, hypothesis, ["Claude Code"]).accuracy == 1.0


def test_a_two_word_term_written_as_one_word_is_a_miss():
    result = term_accuracy_v2("open Claude Code now", "open ClaudeCode now", ["Claude Code"])
    assert result.accuracy == 0.0 and result.hypothesis_occurrences == 0


def test_a_term_split_by_an_inserted_word_is_a_miss():
    result = term_accuracy_v2("open Claude Code now", "open Claude new Code now", ["Claude Code"])
    assert result.accuracy == 0.0


def test_two_occurrences_one_in_place():
    reference = "Docker here and Docker there"
    hypothesis = "Docker here and doctor there"
    result = term_accuracy_v2(reference, hypothesis, ["Docker"])
    assert (result.rows[0].expected, result.rows[0].found) == (2, 1)
    assert result.hits == [[True, False]]


def test_plural_still_accepted():
    result = term_accuracy_v2("two pull requests", "two pull requests", ["pull request"])
    assert result.accuracy == 1.0


def test_longer_term_claims_its_tokens_in_both_texts():
    reference = "Claude Code and Claude"
    hypothesis = "Claude Code and Claude"
    result = term_accuracy_v2(reference, hypothesis, ["Claude", "Claude Code"])
    assert {r.term: (r.expected, r.found) for r in result.rows} == {"Claude": (1, 1), "Claude Code": (1, 1)}


def test_empty_hypothesis_scores_zero():
    result = term_accuracy_v2("we use Docker", "", ["Docker"])
    assert result.accuracy == 0.0 and result.precision is None


# --- averages --------------------------------------------------------------------------


def test_macro_weighs_every_term_the_same_and_without_top_drops_the_dominant_term():
    reference = " and ".join(["Docker"] * 8 + ["Kubernetes", "Terraform"])
    hypothesis = " and ".join(["Docker"] * 8 + ["kubectl", "terraform"])
    result = term_accuracy_v2(reference, hypothesis, ["Docker", "Kubernetes", "Terraform"])
    assert result.accuracy == pytest.approx(9 / 10)  # micro: Docker carries it
    assert result.macro == pytest.approx((1 + 0 + 1) / 3)
    assert result.top_term == "Docker"
    assert result.micro_without_top == pytest.approx(1 / 2)


def test_without_top_is_undefined_for_a_single_term():
    result = term_accuracy_v2("Docker", "Docker", ["Docker"])
    assert result.micro_without_top is None and result.macro == 1.0


def test_a_tie_for_the_top_term_takes_the_first_in_glossary_order():
    result = term_accuracy_v2("Docker and Terraform", "Docker and terra form", ["Terraform", "Docker"])
    assert result.top_term == "Terraform" and result.micro_without_top == 1.0


# --- Wilson interval, verdict, overfitting signal ------------------------------------


def test_wilson_interval_known_values():
    low, high = metrics.wilson_interval(0, 10)
    assert low == 0.0 and high == pytest.approx(0.2775, abs=1e-4)
    low, high = metrics.wilson_interval(66, 72)
    assert low == pytest.approx(0.8299, abs=1e-3) and high == pytest.approx(0.9617, abs=1e-3)
    with pytest.raises(ValueError):
        metrics.wilson_interval(3, 0)


@pytest.mark.parametrize(("lower", "upper", "wilson_low", "verdict"), [
    (0.95, 0.97, 0.87, metrics.PASS_DEMONSTRATED),
    (0.90, 0.95, 0.80, metrics.PASS_NOT_DEMONSTRATED),
    (0.80, 0.84, 0.70, metrics.FAIL),
    (0.80, 0.90, 0.70, metrics.INCONCLUSIVE),
])
def test_gate_verdict(lower, upper, wilson_low, verdict):
    assert metrics.gate_verdict(lower, upper, wilson_low) == verdict


def test_overfitting_signal_is_more_than_ten_points_below_the_t2_mean():
    t2 = [0.96, 0.95, 0.97]  # mean 0.96
    assert metrics.overfitting_signal(0.85, t2)
    assert not metrics.overfitting_signal(0.87, t2)
    with pytest.raises(ValueError):
        metrics.overfitting_signal(0.9, [])


def test_bounds_count_a_certain_occurrence_only_in_place():
    j = "we start Docker then talk then stop Docker"
    hypothesis = "we start doctor then talk Docker then stop Docker"
    result = metrics.bounded_term_accuracy(j, j, hypothesis, ["Docker"], [])
    assert (result.occurrences, result.certain_hits) == (2, 1)
    assert result.lower == result.upper == pytest.approx(1 / 2)
