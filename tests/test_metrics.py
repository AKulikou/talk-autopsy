"""Metrics on synthetic texts with known answers; no audio, no network."""

import pytest

from talk_autopsy import metrics
from talk_autopsy.metrics import (
    false_edit_rate,
    is_identifier,
    reading_speed,
    term_accuracy,
    tokenize,
    word_error_rate,
)

# --- tokenize --------------------------------------------------------------


def test_punctuation_is_stripped_from_word_edges_and_case_lowered():
    assert tokenize("Hello, World! It's   fine.\nReally?") == ["hello", "world", "it's", "fine", "really"]


@pytest.mark.parametrize("text, token", [
    ("run kubectl, then", "kubectl"),
    ("the (useEffect) hook", "useEffect"),
    ("install @anthropic-ai/claude-code.", "@anthropic-ai/claude-code"),
    ("edit ~/.claude/settings.json,", "~/.claude/settings.json"),
    ("type /clear now", "/clear"),
    ("copy .env.example", ".env.example"),
    ("GPT-5.", "GPT-5"),
    ("written in C++.", "C++"),
    ("about 50%,", "50%"),
    ('"src/"', "src/"),
])
def test_identifiers_survive_as_one_token(text, token):
    assert token in tokenize(text, lowercase=False)


def test_shell_command_keeps_its_flag():
    assert tokenize("npm install -g @anthropic-ai/claude-code") == [
        "npm", "install", "-g", "@anthropic-ai/claude-code"
    ]
    assert tokenize("uv run --steps stt") == ["uv", "run", "--steps", "stt"]


def test_dashes_ellipses_and_markers_are_not_words():
    text = "so - this ... works \u2014 really\u2026 [inaudible] ...and [Laughter]"
    assert tokenize(text) == ["so", "this", "works", "really", "and"]


def test_typographic_apostrophe_matches_ascii():
    assert tokenize("don\u2019t") == tokenize("don't") == ["don't"]


def test_nothing_is_expanded():
    assert tokenize("10 ten open-source open source") == ["10", "ten", "open-source", "open", "source"]


def test_lowercase_off_keeps_case():
    assert tokenize("Claude Code uses useEffect", lowercase=False) == ["Claude", "Code", "uses", "useEffect"]


# --- WER -------------------------------------------------------------------


def test_wer_counts_each_kind_of_error():
    # reference 10 words; hypothesis: 1 insertion (so), 1 substitution (fox->box), 1 deletion (lazy)
    ref = "The quick brown fox jumps over the lazy old dog."
    hyp = "so the quick brown box jumps over the old dog"
    result = word_error_rate(ref, hyp)
    assert result.reference_words == 10
    assert (result.substitutions, result.deletions, result.insertions) == (1, 1, 1)
    assert result.hits == 8
    assert result.wer == pytest.approx(0.3)


def test_wer_ignores_case_and_punctuation():
    assert word_error_rate("Run kubectl, then npm.", "run Kubectl then NPM").wer == 0


def test_wer_counts_a_broken_identifier():
    result = word_error_rate("call useEffect here", "call use effect here")
    assert (result.substitutions, result.insertions) == (1, 1)
    assert result.wer == pytest.approx(2 / 3)


def test_wer_needs_reference_words():
    with pytest.raises(ValueError, match="no words"):
        word_error_rate(" [inaudible] ... ", "anything")


# --- term accuracy ---------------------------------------------------------


@pytest.mark.parametrize("token, expected", [
    ("useEffect", True), ("GitHub", True), ("API", True), ("GPT-5", True), ("k8s", True),
    ("claude-code", True), ("-g", True), (".env", True), ("@scope", True), ("src/app", True),
    ("npm", False), ("Claude", False), ("subagent", False), ("Kubernetes", False),
])
def test_identifier_rule(token, expected):
    assert is_identifier(token) is expected


def test_term_appearing_twice_and_found_once():
    ref = "Open Claude Code. Later, Claude Code runs the subagent."
    hyp = "Open Claude Code. Later, cloud code runs the sub agent."
    result = term_accuracy(ref, hyp, ["Claude Code", "subagent"])
    rows = {row.term: row for row in result.rows}
    assert (rows["Claude Code"].expected, rows["Claude Code"].found) == (2, 1)
    assert (rows["subagent"].expected, rows["subagent"].found) == (1, 0)
    assert result.accuracy == pytest.approx(1 / 3)
    assert (result.found, result.expected) == (1, 3)


def test_ordinary_words_match_any_case_identifiers_only_exact_case():
    ref = "Use npm and GitHub with useEffect."
    hyp = "use NPM and Github with useeffect"
    rows = {row.term: row for row in term_accuracy(ref, hyp, ["npm", "GitHub", "useEffect"]).rows}
    assert rows["npm"].found == 1  # ordinary word: case-insensitive
    assert rows["GitHub"].found == 0  # identifier: case-sensitive
    assert rows["useEffect"].found == 0
    assert rows["GitHub"].identifier and not rows["npm"].identifier


def test_a_term_inside_a_longer_term_is_not_counted_twice():
    ref = "Ask Claude. Claude Code runs GitHub Actions on GitHub."
    hyp = "Ask Claude. Cloud Code runs GitHub Actions on GitHub."
    terms = ["Claude", "GitHub", "Claude Code", "GitHub Actions"]  # glossary order is kept in the rows
    rows = {row.term: (row.expected, row.found) for row in term_accuracy(ref, hyp, terms).rows}
    assert rows == {"Claude": (1, 1), "GitHub": (1, 1), "Claude Code": (1, 0), "GitHub Actions": (1, 1)}
    assert [row.term for row in term_accuracy(ref, hyp, terms).rows] == terms


def test_terms_are_whole_tokens_not_substrings():
    result = term_accuracy("we use npm here", "we use pnpm here", ["npm"])
    assert result.rows[0].found == 0


def test_extra_occurrences_in_the_hypothesis_do_not_count_twice():
    result = term_accuracy("npm once", "npm npm npm", ["npm"])
    assert (result.rows[0].expected, result.rows[0].found) == (1, 1)
    assert result.accuracy == 1


def test_terms_absent_from_the_reference_are_excluded():
    result = term_accuracy("only kubectl here", "only kubectl here", ["kubectl", "Docker"])
    assert [row.term for row in result.rows] == ["kubectl"]
    assert result.absent == ["Docker"]
    assert result.accuracy == 1


def test_no_term_in_the_reference_gives_no_average():
    result = term_accuracy("nothing relevant", "nothing relevant", ["Docker"])
    assert result.accuracy is None and result.rows == []


# --- false-edit rate -------------------------------------------------------


def test_false_edit_rate_v1_is_a_raw_hit_that_the_fix_broke():
    ref = tokenize("we run the claude code agent today")
    raw = tokenize("we run the cloud code agent today")  # 6 hits, one error
    fixed = tokenize("we ran the claude code agent today")  # fixes cloud, breaks run
    result = metrics.false_edit_rate_v1(raw, fixed, ref)
    assert (result.false_edits, result.raw_hits) == (1, 6)
    assert result.rate == pytest.approx(1 / 6)


def test_false_edit_rate_is_a_share_of_the_corrections_edits():
    ref = tokenize("we run the claude code agent today")
    raw = tokenize("we run the cloud code agent today")
    fixed = tokenize("we ran the claude code agent today")  # two edits: one helps, one hurts
    result = false_edit_rate(raw, fixed, ref)
    assert (result.edits, result.false_edits, result.helpful_edits) == (2, 1, 1)
    assert result.rate == pytest.approx(1 / 2)


def test_a_fix_that_only_corrects_has_no_false_edits():
    ref = tokenize("we run the claude code agent")
    raw = tokenize("we run the cloud code agent")
    result = false_edit_rate(raw, ref, ref)
    assert result.false_edits == 0 and result.rate == 0 and result.edits == 1


def test_invented_insertions_are_false_edits():
    # The auditor's probe: v1 scored a correction that only inserts invented words as 0.
    ref = tokenize("we run the agent")
    raw = list(ref)
    fixed = tokenize("we run the shiny new agent")
    assert metrics.false_edit_rate_v1(raw, fixed, ref).rate == 0
    result = false_edit_rate(raw, fixed, ref)
    assert (result.edits, result.false_edits) == (2, 2) and result.rate == 1.0


def test_inserted_words_that_the_reference_has_are_helpful():
    ref = tokenize("open Claude Code now")
    raw = tokenize("open Quadcode now")
    fixed = tokenize("open Claude Code now")  # Quadcode -> Claude, + Code
    result = false_edit_rate(raw, fixed, ref)
    assert (result.edits, result.false_edits, result.helpful_edits) == (2, 0, 2)


def test_false_edit_rate_without_edits_is_undefined():
    result = false_edit_rate(["a", "b"], ["a", "b"], ["a", "c"])
    assert result.edits == 0 and result.rate is None
    assert metrics.false_edit_rate_v1(["x", "y"], ["a", "b"], ["a", "b"]).rate is None


def test_correction_edits_lists_substitutions_deletions_and_insertions():
    edits = metrics.correction_edits(["a", "b", "c"], ["a", "x", "c", "d"])
    assert sorted(edits) == [("ins", 3, "d"), ("sub", 1, "x")]
    assert metrics.correction_edits(["a", "b"], ["b"]) == [("del", 0, None)]
    assert metrics.correction_edits([], ["a"]) == [("ins", 0, "a")]


# --- timing drift, reading speed -------------------------------------------


def test_timing_drift_is_not_implemented_yet():
    with pytest.raises(NotImplementedError, match="hand-labelled"):
        metrics.timing_drift([], [])


def test_reading_speed_per_cue_and_share_above_threshold():
    cues = [
        ("ten chars!", 0.0, 1.0),  # 10 cps
        ("twenty  characters\nhere", 1.0, 2.0),  # "twenty characters here" = 22 cps
        ("a" * 34, 2.0, 4.0),  # 17 cps: at the threshold, not above it
    ]
    result = reading_speed(cues)
    assert result.cps == pytest.approx([10, 22, 17])
    assert result.share_above == pytest.approx(1 / 3)
    assert result.max_cps == pytest.approx(22)
    assert reading_speed(cues, threshold=12).share_above == pytest.approx(2 / 3)


@pytest.mark.parametrize("cues", [[("text", 2.0, 2.0)], [("text", 3.0, 1.0)], []])
def test_reading_speed_rejects_bad_cues(cues):
    with pytest.raises(ValueError):
        reading_speed(cues)


# --- reference conventions: fillers and plurals ----------------------------


def test_filler_sounds_are_removed_in_any_case():
    assert tokenize("Um, so uh I mean, er... Hmm. Ah, you know, like this") == [
        "so", "i", "mean", "you", "know", "like", "this"]


def test_fillers_never_count_as_wer_errors():
    result = word_error_rate("open the terminal", "Um, open uh the terminal, hmm.")
    assert result.wer == 0 and result.insertions == 0


def test_is_filler_ignores_edge_punctuation():
    assert metrics.is_filler("Um,") and metrics.is_filler("hmm...") and not metrics.is_filler("umbrella")


@pytest.mark.parametrize("term, spoken", [
    ("diff", "two diffs"),
    ("pull request", "open pull requests"),
    ("library", "three libraries"),
    ("box", "the boxes"),
])
def test_regular_plural_of_the_last_word_matches_the_term(term, spoken):
    result = term_accuracy(spoken, spoken, [term])
    assert result.rows[0].expected == 1 and result.accuracy == 1.0


def test_plural_is_accepted_only_on_the_last_word():
    assert term_accuracy("pulls request", "pulls request", ["pull request"]).rows == []


def test_plural_keeps_the_identifier_case_rule():
    result = term_accuracy("two PRs", "two prs", ["PR"])
    assert result.rows[0].found == 0


def test_plural_forms():
    assert metrics.plural_forms("library") == ["librarys", "libraryes", "libraries"]
    assert "keies" not in metrics.plural_forms("key")
