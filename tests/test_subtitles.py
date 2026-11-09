"""Subtitle cues: splitting, line limits, timing, SRT text."""

import pytest
from typer.testing import CliRunner

from talk_autopsy import cli, subtitles
from talk_autopsy.glossary import Glossary, GlossaryEntry
from talk_autopsy.segments import Segment, Transcript, Word, save_transcript
from talk_autopsy.subtitles import build_cues, srt_text, timestamp, wrap

runner = CliRunner()


def timed(segment_id: int, start: float, text: str, step: float = 0.3) -> Segment:
    """A segment whose words are `step` seconds apart, each lasting `step` - 0.05 s."""
    words = [Word(start=start + n * step, end=start + n * step + step - 0.05, text=w)
             for n, w in enumerate(text.split())]
    return Segment(id=segment_id, start=words[0].start, end=words[-1].end, text=text, words=words)


def transcript(*segments: Segment) -> Transcript:
    return Transcript(segments=list(segments), language="en", source="test")


def test_wrap_balances_two_lines():
    text = "We open the terminal and then we start Claude Code in the repo"
    lines = wrap(text)
    assert len(lines) == 2 and all(len(line) <= 42 for line in lines)
    assert " ".join(lines) == text
    assert abs(len(lines[0]) - len(lines[1])) <= 8
    assert wrap("short line") == ["short line"]


def test_cues_respect_line_and_duration_limits():
    text = " ".join(["word"] * 60)
    cues = build_cues(transcript(timed(1, 0.0, text, step=0.4)))
    assert all(len(c.lines) <= 2 and all(len(line) <= 42 for line in c.lines) for c in cues)
    assert all(c.end - c.start <= 7.0 + 1e-9 for c in cues)
    assert " ".join(c.text for c in cues) == text


def test_a_sentence_end_is_preferred_over_a_later_word_boundary():
    text = "This is the first sentence. And here comes a second one that is long enough to need a split"
    cues = build_cues(transcript(timed(1, 0.0, text)))
    assert cues[0].text == "This is the first sentence."


def test_a_clause_end_is_preferred_when_there_is_no_sentence_end():
    text = "When the agent finishes the task, it opens a pull request on GitHub for the whole team to review"
    cues = build_cues(transcript(timed(1, 0.0, text)))
    assert cues[0].text == "When the agent finishes the task,"


def test_no_break_inside_a_glossary_term():
    # without the glossary the 84-character limit would break between "pull" and "request"
    text = "and after all of that work is done the agent will go ahead and open a pull request on GitHub"
    plain = build_cues(transcript(timed(1, 0.0, text, step=0.1)))
    assert plain[0].text.endswith("pull")
    glossary = Glossary(entries=[GlossaryEntry(term="pull request", keep=False)])
    cues = build_cues(transcript(timed(1, 0.0, text, step=0.1)), glossary)
    assert not any(c.text.endswith("pull") for c in cues)
    assert any("pull request" in c.text for c in cues)


def test_times_come_from_words_and_short_cues_are_extended_without_overlap():
    t = transcript(timed(1, 1.0, "Yes."), timed(2, 1.5, "Then we go on with the demo."))
    first, second = build_cues(t)
    assert first.start == 1.0 and first.end == pytest.approx(1.5 - 0.04)  # cannot reach 1 s
    assert second.start == 1.5
    assert second.end == pytest.approx(1.5 + 6 * 0.3 + 0.25 + 0.3)  # last word end + linger


def test_a_lone_cue_gets_at_least_a_second():
    (cue,) = build_cues(transcript(timed(1, 0.0, "Hi.")))
    assert cue.end - cue.start == pytest.approx(1.0)


def test_without_word_timings_time_is_shared_by_characters():
    segment = Segment(id=1, start=10.0, end=20.0,
                      text="Это первое предложение перевода. А это второе предложение, оно немного длиннее.")
    cues = build_cues(transcript(segment))
    assert len(cues) == 2 and cues[0].start == 10.0
    assert cues[0].text == "Это первое предложение перевода."
    assert 13.0 < cues[1].start < 15.0


def test_srt_text_and_timestamps():
    assert timestamp(3723.456) == "01:02:03,456"
    cues = build_cues(transcript(timed(1, 0.0, "Hello there.")))
    assert srt_text(cues) == "1\n00:00:00,000 --> 00:00:01,000\nHello there.\n"


def test_report_counts_reading_speed(tmp_path):
    report = subtitles.write_srt(transcript(timed(1, 0.0, "Hello there.")), tmp_path / "a.srt")
    assert report.cues == 1 and report.reading.cps == [pytest.approx(12.0)]
    assert report.shorter_than_min == 0 and report.lines_too_long == 0


def test_srt_command(tmp_path):
    source = save_transcript(transcript(timed(1, 0.0, "Hello there.")), tmp_path / "fixed.json")
    out = tmp_path / "a.en.srt"
    result = runner.invoke(cli.app, ["srt", str(source), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8").startswith("1\n00:00:00,000")
    assert "1 cues" in result.output and "cps" in result.output
