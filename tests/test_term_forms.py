"""E1's form-normalized term accuracy (talk_autopsy/term_forms.py)."""

from talk_autopsy import metrics, term_forms

TERMS = ["MoodNFT", "Encoding.sol", "SimpleStorage.abi", "make deploy-mood", "NETWORK_ARGS", "gas price"]


def test_case_and_separators_are_ignored():
    ref = ("Open Encoding.sol, build MoodNFT, read SimpleStorage.abi, "
           "run make deploy-mood with NETWORK_ARGS.")
    hyp = ("Open encoding.sol, build Mood NFT, read simple_storage.abi, "
           "run make deploy mood with Network args.")
    assert metrics.term_accuracy_v2(ref, hyp, TERMS).accuracy < 0.5
    assert term_forms.term_accuracy(ref, hyp, TERMS).accuracy == 1.0


def test_nothing_else_is_forgiven():
    ref = "Open Encoding.sol and check the gas price."
    assert term_forms.term_accuracy(ref, "Open Encoding.js and check the gas prize.", TERMS).accuracy == 0.0
    assert term_forms.term_accuracy(ref, "Open Encoding sol and check the gas price.", TERMS).accuracy == 0.5
    # the position still matters: the term elsewhere is not a hit
    moved = term_forms.term_accuracy("MoodNFT one two three", "one two three Mood NFT", ["MoodNFT"])
    assert moved.accuracy == 0.0


def test_unchanged_text_scores_as_the_frozen_metric():
    ref = "We deploy MoodNFT and check the gas price."
    frozen = metrics.term_accuracy_v2(ref, ref, TERMS).accuracy
    assert term_forms.term_accuracy(ref, ref, TERMS).accuracy == frozen
