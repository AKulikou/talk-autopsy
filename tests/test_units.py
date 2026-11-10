"""Sentence units: merging, splitting, word timings, source ids."""

import pytest

from talk_autopsy.segments import Segment, Transcript, Word
from talk_autopsy.units import make_units, segment_words


def timed(segment_id: int, start: float, text: str, step: float = 0.3) -> Segment:
    """A segment whose words are `step` seconds apart, each lasting `step` - 0.05 s."""
    words = [Word(start=start + n * step, end=start + n * step + step - 0.05, text=w)
             for n, w in enumerate(text.split())]
    return Segment(id=segment_id, start=words[0].start, end=words[-1].end, text=text, words=words)


def transcript(*segments: Segment) -> Transcript:
    return Transcript(segments=list(segments), language="en", source="fixed")


def test_segments_merge_until_terminal_punctuation():
    t = transcript(timed(1, 0.0, "If you want to"), timed(2, 1.3, "try it out,"),
                   timed(3, 2.3, "then this is your chance."), timed(4, 4.0, "Next one."))
    units = make_units(t)
    assert [u.source_ids for u in units.segments] == [[1, 2, 3], [4]]
    first = units.segments[0]
    assert first.id == 1 and first.text == "If you want to try it out, then this is your chance."
    assert first.start == 0.0 and first.end == t.segments[2].end
    assert [w.text for w in first.words] == first.text.split()  # the English words keep their times
    assert first.words[4].start == pytest.approx(1.3)
    meta = units.meta["units"]
    assert meta["segments"] == 4 and meta["units"] == 2 and meta["merged_units"] == 1
    assert meta["split_segments"] == 0


def test_a_long_pause_ends_a_unit_without_punctuation():
    t = transcript(timed(1, 0.0, "and then"), timed(2, 0.55 + 1.6, "we stop"),
                   timed(3, 2.15 + 0.55 + 1.4, "here."))
    units = make_units(t)
    # 1.6 s after segment 1 ends the unit; 1.4 s after segment 2 does not
    assert [u.source_ids for u in units.segments] == [[1], [2, 3]]


def test_every_sentence_segment_stays_its_own_unit():
    t = transcript(timed(1, 0.0, "One."), timed(2, 0.7, "Two?"), timed(3, 1.4, "Three!"))
    units = make_units(t)
    assert [u.source_ids for u in units.segments] == [[1], [2], [3]]
    assert [u.text for u in units.segments] == ["One.", "Two?", "Three!"]
    assert [(u.start, u.end) for u in units.segments] == [(s.start, s.end) for s in t.segments]


def test_a_unit_over_25_s_is_split_at_a_clause_end_near_the_middle():
    # 100 words, 0.3 s apart: about 30 s, no sentence end until the last word
    words = [f"w{n}" for n in range(100)]
    words[29] += ","  # a clause end at about 9 s
    words[49] += ","  # a clause end at about 15 s: nearest the middle
    words[79] += ","  # a clause end at about 24 s
    words[-1] += "."
    t = transcript(timed(7, 0.0, " ".join(words)))
    units = make_units(t)
    assert len(units.segments) == 2
    first, second = units.segments
    assert first.text.endswith("w49,") and second.text.startswith("w50")
    assert all(u.end - u.start <= 25.0 for u in units.segments)
    assert first.source_ids == second.source_ids == [7]  # one segment in two units
    assert units.meta["units"]["split_segments"] == 1


def test_the_split_prefers_a_sentence_end_then_a_segment_boundary():
    # segments of 6 words, 1.8 s each; no punctuation but the very end, no long pauses
    segments = [timed(n + 1, n * 1.8, " ".join(["word"] * 6)) for n in range(17)]
    segments[-1] = timed(17, 16 * 1.8, " ".join(["word"] * 5 + ["end."]))
    units = make_units(transcript(*segments))
    assert len(units.segments) == 2
    first, second = units.segments
    assert len(first.text.split()) % 6 == 0  # broken at a segment boundary, not inside a segment
    assert not set(first.source_ids) & set(second.source_ids)

    sentence = [timed(n + 1, n * 1.8, " ".join(["word"] * 6)) for n in range(17)]
    sentence[3] = timed(4, 3 * 1.8, "word word Stop. word word word")
    sentence[-1] = timed(17, 16 * 1.8, " ".join(["word"] * 5 + ["end."]))
    # "Stop." ends a sentence inside segment 4, which itself does not end with one, so all 17
    # segments form one 30.6-second unit; its sentence end beats every segment boundary
    units = make_units(transcript(*sentence))
    assert len(units.segments) == 2 and units.segments[0].text.endswith("Stop.")
    assert units.segments[1].source_ids[0] == 4  # segment 4 is in both units
    assert all(u.end - u.start <= 25.0 for u in units.segments)


def test_a_segment_whose_words_do_not_match_its_text_gets_times_by_characters():
    segment = Segment(id=3, start=10.0, end=12.0, text="aaa bbbbbbb",
                      words=[Word(start=10.0, end=12.0, text="something")])
    words = segment_words(segment)
    assert [w.text for w in words] == ["aaa", "bbbbbbb"]
    assert words[0].start == 10.0 and words[0].end == pytest.approx(10.0 + 2.0 * 4 / 12, abs=1e-3)
    assert words[1].end == pytest.approx(12.0)


def test_a_segment_without_text_joins_no_unit():
    t = transcript(timed(1, 0.0, "Hello."), Segment(id=2, start=1.0, end=1.2, text=""), timed(3, 1.5, "Bye."))
    units = make_units(t)
    assert [u.source_ids for u in units.segments] == [[1], [3]]
