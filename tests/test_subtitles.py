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


def test_times_come_from_words_and_cues_are_extended_without_overlap():
    t = transcript(timed(1, 1.0, "Yes."), timed(2, 1.5, "Then we go on with the demo."))
    first, second = build_cues(t)
    assert first.start == 1.0 and first.end == pytest.approx(1.5 - 0.1)  # stops 0.1 s before the next cue
    assert second.start == 1.5
    assert second.end == pytest.approx(1.5 + 6 * 0.3 + 0.25 + 1.5)  # last word end + 1.5 s


def test_a_lone_cue_stays_1_5_s_after_its_last_word():
    (cue,) = build_cues(transcript(timed(1, 0.0, "Hi.")))
    assert cue.end - cue.start == pytest.approx(0.25 + 1.5)


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
    assert srt_text(cues) == "1\n00:00:00,000 --> 00:00:02,050\nHello there.\n"


def test_report_counts_reading_speed(tmp_path):
    report = subtitles.write_srt(transcript(timed(1, 0.0, "Hello there.")), tmp_path / "a.srt")
    assert report.cues == 1 and report.reading.cps == [pytest.approx(12 / 2.05)]
    assert report.shorter_than_min == 0 and report.lines_too_long == 0
    assert report.share_above_17 == 0 and report.share_above_25 == 0


def test_report_shares_above_17_and_25_cps():
    # each cue stays 1.5 s after its segment: 2.5 s, 2.5 s and 3.5 s on screen
    t = transcript(Segment(id=1, start=0.0, end=1.0, text="x" * 45),  # 18.0 cps
                   Segment(id=2, start=5.0, end=6.0, text="y" * 66),  # 26.4 cps
                   Segment(id=3, start=10.0, end=12.0, text="z" * 20))  # 5.7 cps
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
    assert len(events) == 2 and events[0].startswith("Dialogue: 0,0:00:00.00,0:00:02.05,Default,,0,0,0,,")
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


# --- target text timed by the English words of a sentence unit -----------------------


def unit(text: str, english: list[tuple[str, float, float]]) -> Segment:
    words = [Word(start=s, end=e, text=w) for w, s, e in english]
    return Segment(id=1, start=words[0].start, end=words[-1].end, text=text, words=words, source_ids=[1, 2])


def test_a_translated_unit_is_timed_by_its_english_words_not_by_characters():
    # two English clauses with a 3-second pause between them; the target text has a clause end too
    english = [("We", 0.0, 0.4), ("open", 0.5, 0.9), ("the", 1.0, 1.4), ("terminal,", 1.5, 2.0),
               ("and", 5.0, 5.4), ("then", 5.5, 6.4), ("we", 6.5, 7.4), ("run", 7.5, 8.4),
               ("tests.", 8.5, 9.0)]
    text = "Открываем терминал, а потом запускаем все тесты."
    (first, second) = build_cues(transcript(unit(text, english)))
    assert first.text == "Открываем терминал," and second.text == "а потом запускаем все тесты."
    assert first.start == 0.0
    assert second.start == 5.0  # the English "and": the pause falls between the two cues
    assert first.end == pytest.approx(2.0 + 1.5)  # "terminal," ends at 2.0 s, then +1.5 s of display
    assert second.end == pytest.approx(9.0 + 1.5)
    # by characters the first cue would have ended at about 3.6 s, inside the pause
    by_chars = build_cues(transcript(Segment(id=1, start=0.0, end=9.0, text=text)))
    assert by_chars[1].start == pytest.approx(9.0 * 20 / 49, abs=0.01)


def test_cue_spans_are_proportional_to_the_english_words():
    # ten English words, one per second; the target has no punctuation, so no snapping
    english = [(f"w{n}", float(n), n + 0.9) for n in range(10)]
    text = " ".join(["слово"] * 30)  # 30 equal words over 10 s: too long for 7 s, two cues
    cues = build_cues(transcript(unit(text, english)))
    assert len(cues) == 2
    share = len(cues[0].text) / len(text)
    boundary = round(share * 10)  # the second cue starts at that English word
    assert cues[1].start == pytest.approx(float(boundary))
    assert cues[0].end <= cues[1].start - 0.1 + 1e-9


def test_fewer_english_words_than_cues_falls_back_to_characters():
    english = [("Yes,", 0.0, 0.5), ("sure.", 0.6, 9.0)]
    text = " ".join(["слово"] * 40)
    cues = build_cues(transcript(unit(text, english)))
    assert len(cues) >= 3
    assert all(c.end > c.start for c in cues)
    assert all(a.end <= b.start for a, b in zip(cues, cues[1:], strict=False))


def test_display_extension_limits():
    cues = [subtitles.Cue(0, 0.0, 6.5, ["a"], 1),  # 7 s cap: ends at 7.0, not 8.0
            subtitles.Cue(0, 9.0, 9.5, ["b"], 2),  # 0.1 s before the next cue: 10.0
            subtitles.Cue(0, 10.1, 10.5, ["c"], 3),  # +1.5 s: 12.0
            subtitles.Cue(0, 20.0, 21.0, ["d"], 4),  # cut back: reaches into the next cue
            subtitles.Cue(0, 20.8, 22.0, ["e"], 5)]  # last: +1.5 s
    subtitles.extend_display(cues)
    assert [c.end for c in cues] == [7.0, 10.0, 12.0, 20.8, 23.5]
    assert [c.index for c in cues] == [1, 2, 3, 4, 5]


def test_cues_never_overlap_on_a_dense_transcript():
    segments = [timed(n + 1, n * 1.1, "Short one here.") for n in range(30)]
    cues = build_cues(transcript(*segments))
    assert all(a.end <= b.start for a, b in zip(cues, cues[1:], strict=False))
    assert all(a.end <= b.start - 0.1 + 1e-9 for a, b in zip(cues, cues[1:], strict=False))
