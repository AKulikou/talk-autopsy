"""Correction against the fake Claude client: request shape, validator, block failure, meta."""

import re

import pytest

from talk_autopsy import blocks, correct
from talk_autopsy.correct import (
    CorrectionAnswer,
    Edit,
    SegmentText,
    changed_share,
    kept_share,
    moved_runs,
    validate_block,
)
from talk_autopsy.glossary import Glossary, GlossaryEntry
from talk_autopsy.segments import Segment, Transcript, Word
from tests.conftest import response

GLOSSARY = Glossary(entries=[GlossaryEntry(term="Claude Code", keep=True),
                             GlossaryEntry(term="tmux", keep=True)])


def transcript(*texts: str, step: float = 10.0) -> Transcript:
    segments = [Segment(id=n, start=(n - 1) * step, end=(n - 1) * step + 5, text=t,
                        words=[Word(start=(n - 1) * step, end=(n - 1) * step + 1, text=w) for w in t.split()])
                for n, t in enumerate(texts, start=1)]
    return Transcript(segments=segments, language="en", source="gemini+align",
                      meta={"timestamps": "aligned", "cost_usd": 0.0, "wall_seconds": 60.0})


def requested(user: str) -> list[tuple[int, str]]:
    """(id, text) of the segments to correct (after the 'Segments to correct' line)."""
    body = user.split("Segments to correct:")[1]
    return [(int(i), text) for i, text in re.findall(r"^\[(\d+)\] (.*)$", body, re.M)]


def answer_echo(fix: dict[int, str] | None = None, edits: list[Edit] | None = None):
    """A handler that returns every requested segment, with `fix` applied."""
    fix = fix or {}

    def handle(kwargs):
        pairs = requested(kwargs["messages"][0]["content"])
        segments = [SegmentText(id=i, text=fix.get(i, text)) for i, text in pairs]
        return response(CorrectionAnswer(segments=segments, edits=[e for e in edits or [] if e.id in fix]))
    return handle


# --- validator ---------------------------------------------------------------------


def seg(i, text):
    return Segment(id=i, start=0, end=1, text=text)


def test_changed_share():
    assert changed_share("abc", "abc") == 0.0
    assert changed_share("open cloud code now", "open Claude Code now") < 0.4


def test_validator_accepts_a_small_fix():
    answer = CorrectionAnswer(segments=[SegmentText(id=1, text="Open Claude Code in tmux.")], edits=[])
    assert validate_block([seg(1, "Open cloud code in tmux.")], GLOSSARY, answer) == []


@pytest.mark.parametrize("segments, edits, message", [
    ([SegmentText(id=2, text="x")], [], "ids"),
    ([SegmentText(id=1, text="Something else entirely here now")], [], "without an edit"),
    ([SegmentText(id=1, text="Open Claude Code in tmux and many more words added.")], [], "length"),
    ([SegmentText(id=1, text="Open Claude Code in it.")], [], "glossary terms removed: tmux"),
    ([SegmentText(id=1, text=" ")], [], "empty"),
    ([SegmentText(id=1, text="Open Claude Code in tmux.")],
     [Edit(id=7, before="a", after="b", reason="r")], "not in the block"),
])
def test_validator_rejects(segments, edits, message):
    problems = validate_block([seg(1, "Open Claude Code in tmux.")], GLOSSARY,
                              CorrectionAnswer(segments=segments, edits=edits))
    assert any(message in p for p in problems), problems


def test_a_big_change_with_a_reason_passes_the_share_check():
    answer = CorrectionAnswer(segments=[SegmentText(id=1, text="Use Claude Code.")],
                              edits=[Edit(id=1, before="use cloth cold", after="Use Claude Code",
                                          reason="product name")])
    assert validate_block([seg(1, "use cloth cold.")], Glossary(), answer) == []


# --- the stage ---------------------------------------------------------------------


def test_blocks_of_five_minutes_with_three_context_segments():
    t = transcript(*[f"segment {n}" for n in range(1, 41)], step=10.0)  # 400 s
    parts = blocks.make_blocks(t)
    assert [len(b.segments) for b in parts] == [30, 10]
    assert [s.id for s in parts[1].context] == [28, 29, 30] and parts[0].context == []


def test_correct_applies_fixes_keeps_ids_and_records_edits(fake_claude):
    edits = [Edit(id=2, before="cloud code", after="Claude Code", reason="product name")]
    fake = fake_claude(answer_echo({2: "Start Claude Code now."}, edits))
    t = transcript("Hello there.", "Start cloud code now.", "Bye.")
    result = correct.correct(t, GLOSSARY, blocks.VideoContext(title="A talk"), model="claude-sonnet-5-5")
    assert result.ids() == [1, 2, 3]
    assert [s.text for s in result.segments] == ["Hello there.", "Start Claude Code now.", "Bye."]
    assert result.segments[0].words is not None and result.segments[1].words is None  # changed: words dropped
    assert result.segments[1].start == t.segments[1].start
    c = result.meta["correction"]
    assert c["prompt_version"] == "correction-v2" and c["segments_changed"] == 1
    assert c["edits"][0]["reason"] == "product name" and c["failed_blocks"] == []
    assert result.meta["cost_usd"] == pytest.approx((1000 * 2 + 500 * 10) / 1e6)
    assert result.meta["input_wall_seconds"] == 60.0 and result.meta["timestamps"] == "aligned"
    request = fake.requests[0]
    assert request["model"] == "claude-sonnet-5-5" and request["output_format"] is CorrectionAnswer
    system = request["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "Claude Code" in system[0]["text"] and "Title: A talk" in system[0]["text"]
    assert "fix the words the recognizer got wrong" in system[0]["text"]


def test_context_segments_are_sent_read_only(fake_claude):
    fake = fake_claude(answer_echo())
    t = transcript(*[f"segment {n}" for n in range(1, 41)], step=10.0)
    correct.correct(t, Glossary(), "talk")
    second = fake.requests[1]["messages"][0]["content"]
    assert second.startswith("Context segments (read-only") and "[30] segment 30" in second
    assert requested(second)[0][0] == 31


def test_a_block_that_fails_twice_keeps_its_text_and_is_listed(fake_claude):
    def handle(kwargs):
        return response(CorrectionAnswer(segments=[SegmentText(id=99, text="x")], edits=[]))
    fake = fake_claude(handle)
    t = transcript("Hello there.", "Bye.")
    result = correct.correct(t, GLOSSARY, "talk")
    assert len(fake.requests) == 2  # one retry
    assert [s.text for s in result.segments] == ["Hello there.", "Bye."]
    failed = result.meta["correction"]["failed_blocks"]
    assert failed[0]["ids"] == [1, 2] and "ids" in failed[0]["error"]
    assert result.meta["correction"]["usage"]["input_tokens"] == 2000  # rejected attempts are billed


def test_missing_key_fails_before_any_request(fake_claude, monkeypatch):
    from talk_autopsy.config import ConfigurationError
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    fake = fake_claude(answer_echo())
    with pytest.raises(ConfigurationError):
        correct.correct(transcript("Hello."), Glossary(), "talk")
    assert fake.requests == []


def test_estimate_counts_blocks_and_prices_them():
    t = transcript(*[f"segment number {n} with some words" for n in range(1, 41)], step=10.0)
    e = correct.estimate(t, GLOSSARY, blocks.VideoContext(title="t"), model="claude-sonnet-5-5")
    assert e.blocks == 2 and e.input_tokens > 0 and e.output_tokens > 0
    assert e.usd == pytest.approx((e.input_tokens * 2 + e.output_tokens * 10) / 1e6)


# --- edits stay inside their segment (correction-v2) ---------------------------------


def test_kept_share_counts_words_in_order():
    assert kept_share("how much have you used files", "how much have you used files") == 1.0
    assert kept_share("one two three four", "one two") == 0.5
    assert kept_share("", "anything") == 1.0


def test_a_segment_that_loses_most_words_without_an_edit_is_rejected():
    segments = [seg(1, "How much have you used files for machine learning?")]
    answer = CorrectionAnswer(segments=[SegmentText(id=1, text="How much? Files.")], edits=[])
    problems = validate_block(segments, Glossary(), answer)
    assert any("keeps only" in p for p in problems), problems


def test_a_low_kept_share_passes_when_the_edits_cover_every_change():
    segments = [seg(1, "use cloth cold")]
    answer = CorrectionAnswer(segments=[SegmentText(id=1, text="use Claude Code")],
                              edits=[Edit(id=1, before="cloth cold", after="Claude Code", reason="product")])
    assert not any("keeps only" in p for p in validate_block(segments, Glossary(), answer))


def test_an_edit_that_does_not_cover_the_change_does_not_excuse_it():
    segments = [seg(1, "how much have you used files")]
    answer = CorrectionAnswer(segments=[SegmentText(id=1, text="how many bananas were eaten today")],
                              edits=[Edit(id=1, before="files", after="today", reason="accent")])
    assert any("keeps only" in p for p in validate_block(segments, Glossary(), answer))


def test_words_pulled_from_the_next_segment_are_rejected():
    # The full-T2 defect of correction-v1: a garbled question rebuilt from the next segment.
    segments = [seg(1, "How much have you used files?"),
                seg(2, "Claude Code for machine learning modeling and data analysis?")]
    rebuilt = "How much have you used Claude Code for machine learning modeling?"
    answer = CorrectionAnswer(segments=[SegmentText(id=1, text=rebuilt),
                                        SegmentText(id=2, text=segments[1].text)],
                              edits=[Edit(id=1, before="used files", after=rebuilt, reason="garbled")])
    problems = validate_block(segments, GLOSSARY, answer)
    assert any("segment 1 holds words of a neighbouring segment" in p for p in problems), problems


def test_words_pulled_from_the_read_only_context_are_rejected():
    context = [seg(9, "we ran the whole test suite last night")]
    segments = [seg(10, "and then")]
    answer = CorrectionAnswer(segments=[SegmentText(id=10, text="and then the whole test suite")], edits=[])
    problems = validate_block(segments, Glossary(), answer, context)
    assert any("neighbouring" in p for p in problems), problems


def test_a_term_fix_that_matches_a_neighbour_is_not_a_move():
    assert moved_runs("open quad code and", "open Claude Code and",
                      ["open Claude Code and run it"], GLOSSARY) == []
    assert moved_runs("I sink so", "I think so", ["I think so too"], Glossary()) == []
