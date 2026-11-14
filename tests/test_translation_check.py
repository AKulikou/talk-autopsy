"""The pre-registered translation check: sampler, baseline call (fake caller), rating file, scorer."""

import json
from types import SimpleNamespace

import pytest

from talk_autopsy import translation_check as tc
from talk_autopsy.segments import Segment, Transcript, Word


def transcript(texts, translated=False):
    segments = []
    for n, text in enumerate(texts, start=1):
        start = n * 3.0
        words = [Word(start=start + k * 0.2, end=start + k * 0.2 + 0.1, text=w)
                 for k, w in enumerate(text.split())]
        segments.append(Segment(id=n, start=start, end=start + 2.0, text=text, words=words))
    return Transcript(segments=segments, language="ru" if translated else "en", source="test")


def items(n=30):
    return [tc.Item(unit=k, english=f"English sentence {k} of the check.", translation=f"Перевод {k}.")
            for k in range(1, n + 1)]


class FakeCaller:
    def __init__(self, answers=None):
        self.calls, self.answers, self.spent_usd = [], answers, 0.0

    def generate(self, contents, schema):
        self.calls.append(contents)
        self.spent_usd += 0.001
        default = json.dumps({"translation": f"базовый {len(self.calls)}"})
        text = self.answers.pop(0) if self.answers else default
        return SimpleNamespace(text=text)


def test_units_are_rebuilt_from_fixed_and_paired_by_id():
    fixed = transcript(["First sentence.", "Second one.", "Third."])
    translated = Transcript(segments=[s.model_copy(update={"text": f"ru {s.id}"}) for s in
                                      tc.units_module.make_units(fixed).segments], language="ru", source="t")
    pairs = tc.paired_units(fixed, translated)
    assert [(p.unit, p.translation) for p in pairs] == [(s.id, f"ru {s.id}") for s in translated.segments]
    assert pairs[0].english.startswith("First")


def test_mismatched_units_are_refused():
    fixed = transcript(["First sentence.", "Second one."])
    with pytest.raises(tc.CheckError, match="not the units"):
        tc.paired_units(fixed, transcript(["один"], translated=True))


def test_the_sample_is_seeded_sorted_and_of_the_registered_size():
    first, second = tc.sample(items()), tc.sample(items())
    assert first == second and len(first) == tc.SAMPLE_SIZE == 12
    assert [i.unit for i in first] == sorted(i.unit for i in first)
    assert tc.sample(items(), seed=1) != first
    with pytest.raises(tc.CheckError, match="only 5"):
        tc.sample(items(5))


def test_the_sample_skips_units_under_six_words():
    short = [tc.Item(unit=k, english="Yes, um, that is it.", translation="Да.") for k in range(100, 140)]
    chosen = tc.sample(items(12) + short)  # "um" is a filler: 4 words
    assert [i.unit for i in chosen] == list(range(1, 13))
    with pytest.raises(tc.CheckError, match=r"only 11 units with 6\+ words"):
        tc.sample(items(11) + short)


def test_the_baseline_is_one_plain_request_per_unit():
    caller = FakeCaller()
    out, spent = tc.baseline_translations(items(3), caller, "ru")
    assert out == {1: "базовый 1", 2: "базовый 2", 3: "базовый 3"} and spent == pytest.approx(0.003)
    prompt = caller.calls[0][0]
    assert "natural Russian for subtitles" in prompt and "English sentence 1 of the check." in prompt


def test_a_malformed_baseline_answer_is_an_error():
    with pytest.raises(tc.CheckError, match="malformed"):
        tc.baseline_translations(items(1), FakeCaller(["not json"]), "ru")
    with pytest.raises(tc.CheckError, match="language name"):
        tc.baseline_translations(items(1), FakeCaller(), "xx")


def test_the_rating_file_shuffles_both_systems_and_names_neither():
    chosen = items(4)
    baseline = {i.unit: f"базовый {i.unit}" for i in chosen}
    text, key = tc.rating_file(chosen, baseline)
    assert len(key["items"]) == 8
    systems = [key["items"][str(n)]["system"] for n in range(1, 9)]
    assert systems.count("pipeline") == systems.count("baseline") == 4
    assert systems != ["pipeline"] * 4 + ["baseline"] * 4
    assert "pipeline" not in text and "baseline" not in text and "Gemini" not in text
    assert text.count("Score (1-5):") == 8 and text.count("EN: English sentence 1 of the check.") == 2
    assert text.startswith("# Оценка перевода")


def test_the_two_items_of_a_unit_are_never_adjacent():
    chosen = items(20)
    text, key = tc.rating_file(chosen, {i.unit: f"базовый {i.unit}" for i in chosen})
    units = [key["items"][str(n)]["unit"] for n in range(1, 41)]
    assert all(a != b for a, b in zip(units, units[1:], strict=False))
    # the registered seed's first order has 2 adjacent pairs, its second none
    assert key["seed"] == tc.SEED + 1 and key["shuffles"] == 2


def test_the_order_does_not_depend_on_the_text():
    chosen = items(20)
    _, key_a = tc.rating_file(chosen, {i.unit: "a" for i in chosen})
    other = [tc.Item(i.unit, "other English", "другой") for i in chosen]
    _, key_b = tc.rating_file(other, {i.unit: "b" for i in chosen})
    assert key_a == key_b


def test_one_unit_cannot_be_rated_without_adjacency():
    chosen = items(1)
    with pytest.raises(tc.CheckError):
        tc.rating_file(chosen, {1: "б"})


def test_identical_translations_are_shown_once_and_scored_for_both():
    chosen = items(4)
    baseline = {i.unit: f"базовый {i.unit}" for i in chosen}
    baseline[2] = "  перевод 2 "  # case differs: not shared
    baseline[3] = "Перевод  3"  # equal after normalisation
    text, key = tc.rating_file(chosen, baseline)
    assert key["shared_units"] == [3] and len(key["items"]) == 7 and text.count("Score (1-5):") == 7
    shared = [n for n, m in key["items"].items() if m["system"] == "shared"]
    assert len(shared) == 1 and key["items"][shared[0]]["unit"] == 3
    scores = {int(n): (2 if m["system"] == "shared" else 4 if m["system"] == "pipeline" else 3)
              for n, m in key["items"].items()}
    result = tc.score(scores, key)
    assert result.units == 4
    assert result.pipeline_mean == pytest.approx((4 * 3 + 2) / 4)
    assert result.baseline_mean == pytest.approx((3 * 3 + 2) / 4)


def test_normalisation_drops_punctuation_and_whitespace_only():
    assert tc.normalized("«Привет», — мир!  ") == tc.normalized("Привет мир") == "Привет мир"
    assert tc.normalized("Привет") != tc.normalized("привет")


def filled(text, score_of):
    lines, current = [], None
    for line in text.splitlines():
        if line.startswith("## "):
            current = int(line[3:])
        if line.startswith("Score (1-5):"):
            line = f"Score (1-5): {score_of(current)}"
        lines.append(line)
    return "\n".join(lines)


def test_the_scorer_unblinds_and_applies_the_rule():
    chosen = items(20)
    text, key = tc.rating_file(chosen, {i.unit: "б" for i in chosen})
    scores = tc.parse_scores(filled(text, lambda n: 5 if key["items"][str(n)]["system"] == "pipeline" else 3))
    result = tc.score(scores, key)
    assert (result.pipeline_mean, result.baseline_mean, result.difference) == (5.0, 3.0, 2.0)
    assert result.interval == (2.0, 2.0) and result.met and result.units == 20


def test_the_rule_is_the_pipeline_mean_not_the_difference():
    chosen = items(20)
    text, key = tc.rating_file(chosen, {i.unit: "б" for i in chosen})
    scores = tc.parse_scores(filled(text, lambda n: 3 if key["items"][str(n)]["system"] == "pipeline" else 2))
    result = tc.score(scores, key)
    assert result.difference > 0 and not result.met


def test_missing_or_bad_scores_are_errors():
    chosen = items(2)
    text, key = tc.rating_file(chosen, {i.unit: "б" for i in chosen})
    with pytest.raises(tc.CheckError, match="score missing"):
        tc.parse_scores(text)
    with pytest.raises(tc.CheckError, match="1-5"):
        tc.parse_scores(filled(text, lambda n: 7))
    with pytest.raises(tc.CheckError, match="scores for"):
        tc.score({1: 4}, key)


# --- T7: one variant per replica, with the term question -------------------------------------------

def test_t7_constants_are_the_registered_ones():
    assert (tc.T7_SEED, tc.T7_SAMPLE_SIZE) == (20261114, 20)
    assert (tc.E1_TERM_ACCURACY, tc.E1_BLIND_MEAN, tc.E1_TERM_LOWERED) == (0.90, 4.0, 2)


def test_single_rating_file_shows_only_one_translation_per_unit_and_the_term_question():
    chosen = tc.sample(items(), tc.T7_SAMPLE_SIZE, tc.T7_SEED)
    text, key = tc.rating_file_single(chosen)
    assert len(key["items"]) == 20 and key["seed"] == tc.T7_SEED + 1
    assert sorted(v["unit"] for v in key["items"].values()) == [i.unit for i in chosen]
    assert text.count("Score (1-5):") == 20 and text.count("Term: [ ]") == 20
    assert text.count("Which term:") == 20 and "термин" in text
    assert "baseline" not in text and "pipeline" not in text
    assert tc.rating_file_single(chosen) == (text, key)  # seeded


def fill(text, entries):
    """entries: item number -> (score, term or None)."""
    out, current = [], None
    for line in text.splitlines():
        if line.startswith("## "):
            current = int(line[3:])
        score, term = entries.get(current, (None, None))
        if line == "Score (1-5):" and score is not None:
            line = f"Score (1-5): {score}"
        elif line == "Term: [ ]" and term:
            line = "Term: [x]"
        elif line == "Which term:" and term:
            line = f"Which term: {term}"
        out.append(line)
    return "\n".join(out)


def test_single_scores_are_parsed_and_scored_with_the_term_count():
    chosen = tc.sample(items(), 20, tc.T7_SEED)
    text, key = tc.rating_file_single(chosen)
    entries = {n: (5, None) for n in range(1, 21)}
    entries.update({3: (3, "forge"), 7: (2, "msg.sender"), 9: (4, None)})
    result = tc.score_single(tc.parse_single(fill(text, entries)), key)
    assert result.items == 20 and result.term_lowered == 2 and result.terms == ["forge", "msg.sender"]
    assert result.mean == pytest.approx((17 * 5 + 3 + 2 + 4) / 20)
    assert result.distribution == {5: 17, 4: 1, 3: 1, 2: 1, 1: 0}


def test_cyrillic_x_counts_and_a_flag_without_a_term_is_an_error():
    text, _ = tc.rating_file_single(tc.sample(items(), 20, tc.T7_SEED))
    filled = fill(text, {n: (5, None) for n in range(1, 21)})
    cyrillic = filled.replace("Term: [ ]", "Term: [х]", 1).replace("Which term:", "Which term: anvil", 1)
    assert tc.parse_single(cyrillic)[1].term_lowered
    with pytest.raises(tc.CheckError, match="no term written"):
        tc.parse_single(filled.replace("Term: [ ]", "Term: [x]", 1))
    with pytest.raises(tc.CheckError, match="score missing"):
        tc.parse_single(text)


def test_e1_rule_launches_on_any_one_condition():
    assert tc.e1_launch(0.95, 4.5, 2) == (False, [])
    assert tc.e1_launch(0.8999, 4.5, 0)[0]
    assert tc.e1_launch(0.95, 3.99, 0)[0]
    assert tc.e1_launch(0.95, 4.5, 3)[0]
    assert not tc.e1_launch(0.90, 4.0, 2)[0]  # the thresholds themselves are in the target zone
