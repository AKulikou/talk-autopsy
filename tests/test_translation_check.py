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
    return [tc.Item(unit=k, english=f"English sentence {k}.", translation=f"Перевод {k}.")
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
    assert first == second and len(first) == tc.SAMPLE_SIZE == 20
    assert [i.unit for i in first] == sorted(i.unit for i in first)
    assert tc.sample(items(), seed=1) != first
    with pytest.raises(tc.CheckError, match="only 5"):
        tc.sample(items(5))


def test_the_baseline_is_one_plain_request_per_unit():
    caller = FakeCaller()
    out, spent = tc.baseline_translations(items(3), caller, "ru")
    assert out == {1: "базовый 1", 2: "базовый 2", 3: "базовый 3"} and spent == pytest.approx(0.003)
    prompt = caller.calls[0][0]
    assert "natural Russian for subtitles" in prompt and "English sentence 1." in prompt


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
    assert text.count("Score (1-5):") == 8 and text.count("EN: English sentence 1.") == 2
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
