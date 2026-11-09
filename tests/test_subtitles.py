"""Subtitle cues: splitting, line limits, timing, SRT and .ass text."""

import codecs

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


LONG = ("This is the first sentence of the talk. And here comes a second one that is long enough, "
        "together with the first, to need a split into more than one cue on the screen")


def test_wrap_balances_two_lines():
    text = "We open the terminal and then we start Claude Code in the repo for you"
    lines = wrap(text)
    assert len(lines) == 2 and all(len(line) <= 48 for line in lines)
    assert " ".join(lines) == text
    assert abs(len(lines[0]) - len(lines[1])) <= 8
    assert wrap("short line") == ["short line"]


def test_wrap_uses_a_third_line_only_when_two_do_not_fit():
    text = "We open the terminal and then we start Claude Code in the repo and watch it run all the tests"
    lines = wrap(text)
    assert len(lines) == 2 and all(len(line) <= 48 for line in lines)
    longer = text + " for us, one after another"
    lines = wrap(longer)
    assert len(lines) == 3 and all(len(line) <= 48 for line in lines) and " ".join(lines) == longer


def test_cues_respect_line_and_duration_limits():
    text = " ".join(["word"] * 90)
    cues = build_cues(transcript(timed(1, 0.0, text, step=0.15)))
    assert all(len(c.lines) <= 3 and all(len(line) <= 48 for line in c.lines) for c in cues)
    assert any(len(c.lines) == 3 for c in cues)
    assert all(c.end - c.start <= 7.0 + 1e-9 for c in cues)
    assert " ".join(c.text for c in cues) == text  # nothing is cut: the text is split into more cues


def test_a_sentence_end_is_preferred_over_a_later_word_boundary():
    cues = build_cues(transcript(timed(1, 0.0, LONG, step=0.1)))
    assert cues[0].text == "This is the first sentence of the talk."


def test_a_clause_end_is_preferred_when_there_is_no_sentence_end():
    text = ("When the agent finishes the task that we gave it at the start of the demo, it opens a pull "
            "request on GitHub for the whole team to review and comment on before anything is merged")
    cues = build_cues(transcript(timed(1, 0.0, text, step=0.1)))
    assert cues[0].text == "When the agent finishes the task that we gave it at the start of the demo,"


def test_no_break_inside_a_glossary_term():
    # without the glossary the 3 x 48 character limit breaks between "pull" and "request"
    text = ("and after all of that work is done and the tests pass the agent will go ahead and do exactly "
            "what we asked it to do now and then open a pull request on GitHub for us")
    plain = build_cues(transcript(timed(1, 0.0, text, step=0.1)))
    assert plain[0].text.endswith("pull"), plain[0].text
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
    assert report.share_above_17 == 0 and report.share_above_25 == 0


def test_report_shares_above_17_and_25_cps():
    # each cue lingers 0.3 s after its segment: 1.3 s, 1.3 s and 2.3 s on screen
    t = transcript(Segment(id=1, start=0.0, end=1.0, text="x" * 25),  # 19.2 cps
                   Segment(id=2, start=5.0, end=6.0, text="y" * 40),  # 30.8 cps
                   Segment(id=3, start=10.0, end=12.0, text="z" * 20))  # 8.7 cps
    report = subtitles.report(build_cues(t))
    assert report.share_above_25 == pytest.approx(1 / 3)
    assert report.share_above_17 == pytest.approx(2 / 3) and "33.3% above 25 cps" in report.line()


def test_ass_file_structure(tmp_path):
    t = transcript(timed(1, 0.0, "Hello there."), timed(2, 3.0, "A {brace} and a back" + chr(92) + "slash."))
    subtitles.write_srt(t, tmp_path / "talk.ru.srt")
    raw = (tmp_path / "talk.ru.ass").read_bytes()
    assert raw.startswith(codecs.BOM_UTF8)
    text = raw.decode("utf-8-sig")
    sections = [line for line in text.splitlines() if line.startswith("[")]
    assert sections == ["[Script Info]", "[V4+ Styles]", "[Events]"]
    assert "PlayResX: 1920" in text and "PlayResY: 1080" in text and "WrapStyle: 2" in text
    styles = [line for line in text.splitlines() if line.startswith("Style:")]
    assert len(styles) == 1
    values = styles[0][len("Style: "):].split(",")
    fields = dict(zip(subtitles.ASS_STYLE_FORMAT.split(", "), values, strict=True))
    assert fields["Fontname"] == "Arial" and fields["Fontsize"] == "42" and fields["Alignment"] == "2"
    assert fields["PrimaryColour"] == "&H00FFFFFF" and float(fields["Outline"]) > 0
    events = [line for line in text.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == 2 and events[0].startswith("Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,")
    assert events[1].endswith("A " + chr(92) + "{brace" + chr(92) + "} and a back" + chr(92) * 2 + "slash.")


def test_ass_breaks_lines_like_the_srt():
    cue = subtitles.Cue(1, 0.0, 2.0, ["first line", "second line", "third line"], 1)
    assert ("first line" + chr(92) + "Nsecond line" + chr(92) + "Nthird line") in subtitles.ass_text([cue])
    assert subtitles.ass_timestamp(3723.456) == "1:02:03.46"


def test_srt_command(tmp_path):
    source = save_transcript(transcript(timed(1, 0.0, "Hello there.")), tmp_path / "fixed.json")
    out = tmp_path / "a.en.srt"
    result = runner.invoke(cli.app, ["srt", str(source), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8").startswith("1\n00:00:00,000")
    assert "1 cues" in result.output and "cps" in result.output
