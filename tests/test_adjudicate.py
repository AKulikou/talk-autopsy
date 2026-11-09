"""
Three-engine consensus on synthetic transcripts: alignment, spans, the
adjudication file, resolve and the protocol. The CLI test cuts real clips from
generated audio with ffmpeg.
"""

import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from talk_autopsy import adjudicate as adj
from talk_autopsy import cli, config
from talk_autopsy.adjudicate import AdjudicationError, Choice, build_consensus, parse_choices, resolve
from talk_autopsy.segments import Segment, Transcript, Word, save_transcript

runner = CliRunner()
ENGINES = ["faster-whisper", "assemblyai", "gemini"]


def backbone(text: str, pauses: dict[int, float] | None = None) -> Transcript:
    """One word per second (0.5 s long); `pauses` adds seconds of silence before word i."""
    words, t = [], 0.0
    for i, w in enumerate(text.split()):
        t += (pauses or {}).get(i, 0.0)
        words.append(Word(start=t, end=t + 0.5, text=w, probability=0.9))
        t += 1.0
    return Transcript(segments=[Segment(id=1, start=0, end=t, text=text, words=words)], language="en",
                      source="faster-whisper large-v3", meta={"engine": "faster-whisper"})


def hyp(text: str, engine: str) -> Transcript:
    return Transcript(segments=[Segment(id=1, start=0, end=1, text=text)], language="en", source=engine,
                      meta={"engine": engine})


def consensus(a: str, b: str, c: str, audio: float = 60.0, pauses=None):
    hyps = [hyp(b, "assemblyai"), hyp(c, "gemini")]
    return build_consensus("T9", backbone(a, pauses), hyps, audio, ENGINES)


def options(span):
    return [(o.label, o.text) for o in span.options]


def premarked_choices(result) -> dict[int, Choice]:
    return parse_choices(adj.adjudication_markdown(result, "work/T9/clips"))


# --- spans -----------------------------------------------------------------


def test_unanimous_text_has_no_spans_and_is_the_reference():
    result = consensus("We open the terminal.", "we open the Terminal", "We open the terminal!")
    assert result.spans == [] and result.counts()["unanimous_share"] == 1.0
    assert resolve(result, {}).reference == "We open the terminal.\n"


def test_one_engine_substitution_is_a_majority_span_premarked():
    result = consensus("so we open cloud code here today", "so we open Claude Code here today",
                       "so we open Claude Code here today")
    [span] = result.listed
    assert span.verdict == "majority"
    assert options(span) == [("A", "cloud"), ("B+C", "Claude")]  # "code" matches in any case
    assert span.options[span.premarked].label == "B+C"
    assert span.context_before == "so we open" and span.context_after == "code here today"


def test_insertion_by_one_engine():
    result = consensus("we go to the shell now", "we go to the shell really now", "we go to the shell now")
    [span] = result.listed
    assert options(span) == [("A+C", adj.NO_WORDS), ("B", "really")]
    assert span.positions() == range(5, 5)
    assert (span.speech_start, span.speech_end) == (4.5, 5.0)  # the gap between "shell" and "now"


def test_deletion_by_one_engine():
    result = consensus("we go to the shell now", "we go to the shell now", "we go to shell now")
    [span] = result.listed
    assert options(span) == [("A+B", "the"), ("C", adj.NO_WORDS)] and span.verdict == "majority"


def test_three_different_variants_are_a_split_with_nothing_premarked():
    result = consensus("then run slash theme here", "then run /theme here", "then run the theme here")
    [span] = result.listed
    assert span.verdict == "split" and span.premarked is None
    assert options(span) == [("A", "slash theme"), ("B", "/theme"), ("C", "the theme")]


def test_spans_closer_than_three_words_are_merged():
    a = "one two three four five six seven eight nine ten"
    near = consensus(a, "one TWO2 three four FIVE5 six seven eight nine ten", a)
    assert len(near.listed) == 1 and options(near.listed[0])[0] == ("A+C", "two three four five")
    far = consensus(a, "one TWO2 three four five SIX6 seven eight nine ten", a)
    assert len(far.listed) == 2


def test_fillers_do_not_create_disagreements():
    result = consensus("so um we go uh home", "so we go home", "So, we go, hmm, home.")
    assert result.spans == []
    assert resolve(result, {}).reference == "so we go home\n"


def test_letter_case_follows_the_majority_where_engines_agree():
    result = consensus("open Github now", "open GitHub now", "open GitHub. now")
    assert result.spans == []
    assert resolve(result, {}).reference == "open GitHub now\n"
    three_ways = consensus("so Claude code", "so Claude Code", "So Claude CODE")
    assert resolve(three_ways, {}).reference == "so Claude code\n"  # a tie keeps the backbone's case


def test_clip_times_are_padded_and_clipped_to_the_audio():
    result = consensus("alpha beta gamma", "alpha beta delta", "alpha beta delta", audio=3.0)
    [span] = result.listed
    assert (span.speech_start, span.speech_end) == (2.0, 2.5)
    assert (span.start, span.end) == (0.0, 3.0)


# --- adjudication file and resolve -----------------------------------------


def test_adjudication_file_shows_counts_options_and_the_clip():
    result = consensus("then run slash theme here", "then run /theme here", "then run the theme here")
    text = adj.adjudication_markdown(result, "work/T9/clips", link_prefix="../../")
    assert "- split spans: 1" in text and "- majority spans: 0" in text
    assert "### 0001 " in text and " split" in text
    assert "Clip: [work/T9/clips/0001_00-00.wav](../../work/T9/clips/0001_00-00.wav)" in text
    assert "- [ ] B (assemblyai): /theme" in text and "- [ ] other: " in text


CLOUD = ("so we open cloud code here", "so we open Claude Code here", "so we open Claude Code here")


def test_resolve_uses_the_premarked_majority():
    result = consensus(*CLOUD)
    resolution = resolve(result, premarked_choices(result))
    assert resolution.reference == "so we open Claude Code here\n"
    totals = resolution.protocol["totals"]
    assert totals["majority_kept"] == 1 and totals["words_by_engine"] == {
        "faster-whisper": 0, "assemblyai": 1, "gemini": 1}


def test_resolve_with_a_chosen_option_on_a_split():
    result = consensus("then run slash theme here", "then run /theme here", "then run the theme here")
    text = adj.adjudication_markdown(result, "c").replace("- [ ] B (assemblyai)", "- [x] B (assemblyai)")
    assert resolve(result, parse_choices(text)).reference == "then run /theme here\n"


def test_resolve_refuses_an_unresolved_split():
    result = consensus("then run slash theme here", "then run /theme here", "then run the theme here")
    with pytest.raises(AdjudicationError, match="0001 \\(split\\): nothing chosen"):
        resolve(result, premarked_choices(result))


def test_resolve_refuses_two_ticked_boxes():
    result = consensus("then run slash theme here", "then run /theme here", "then run the theme here")
    with pytest.raises(AdjudicationError, match="2 options ticked"):
        resolve(result, {1: Choice(["A", "B"], "")})


def test_other_text_wins_and_counts_as_typed():
    result = consensus(*CLOUD)
    text = adj.adjudication_markdown(result, "c").replace("- [ ] other: ", "- [ ] other: the Claude")
    resolution = resolve(result, parse_choices(text))
    assert resolution.reference == "so we open the Claude Code here\n"
    totals = resolution.protocol["totals"]
    assert totals["typed_words"] == 2 and totals["spans_typed"] == 1 and totals["majority_changed"] == 1


def test_protocol_totals():
    a = "one two three four five six seven eight nine ten"
    result = consensus(a, "one TWO2 three four five six seven EIGHT8 nine ten",
                       "one two three four five six seven EIGHTX nine ten")
    choices = premarked_choices(result)  # span 1 majority (A+C), span 2 split
    choices[2] = Choice(["C"], "")
    resolution = resolve(result, choices)
    assert resolution.reference == "one two three four five six seven EIGHTX nine ten\n"
    totals = resolution.protocol["totals"]
    assert totals["consensus_words"] == 8 and totals["span_words"] == 2 and totals["typed_words"] == 0
    assert totals["words_by_engine"] == {"faster-whisper": 1, "assemblyai": 0, "gemini": 2}
    assert totals["spans_by_engine"] == {"faster-whisper": 1, "assemblyai": 0, "gemini": 2}
    assert totals["reference_words"] == 10
    assert [row["chosen"] for row in resolution.protocol["spans"]] == ["A+C", "C"]


def test_paragraphs_break_at_pauses_longer_than_two_seconds():
    result = consensus("one two three four", "one two three four", "one two three four", pauses={2: 2.6})
    assert resolve(result, {}).reference == "one two\n\nthree four\n"


def test_a_span_missing_from_the_file_is_an_error():
    result = consensus("then run slash theme here", "then run /theme here", "then run the theme here")
    with pytest.raises(AdjudicationError, match="missing"):
        resolve(result, {})


def test_consensus_survives_the_json_round_trip():
    result = consensus("so we open cloud code here", "so we open Claude Code here", "so we open code here")
    again = adj.consensus_from_json(adj.consensus_to_json(result))
    assert again == result


def test_backbone_without_word_timings_is_refused():
    no_words = Transcript(segments=[Segment(id=1, start=0, end=1, text="hello there")], language="en",
                          source="x")
    with pytest.raises(AdjudicationError, match="word timings"):
        build_consensus("T9", no_words, [hyp("a", "b"), hyp("a", "c")], 10.0)


# --- CLI -------------------------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A test set and work folder in tmp_path, with 6 s of generated audio."""
    test_set, work = tmp_path / "data" / "test-set", tmp_path / "work"
    test_set.mkdir(parents=True)
    monkeypatch.setattr(config, "TEST_SET_DIR", test_set)
    monkeypatch.setattr(config, "WORK_DIR", work)
    subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=6", "-ac", "1", "-ar", "16000",
                    str(test_set / "T9.wav")], check=True)
    paths = []
    for name, transcript in (("bb", backbone("then run slash theme here")),
                             ("b", hyp("then run /theme here", "assemblyai")),
                             ("c", hyp("then run the theme here", "gemini"))):
        paths.append(save_transcript(transcript, tmp_path / f"{name}.json"))
    return tmp_path, paths


def test_adjudicate_then_resolve_end_to_end(project):
    root, (bb, b, c) = project
    args = ["adjudicate", "T9", "--backbone", str(bb), "--hyp", str(b), "--hyp", str(c)]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert "1 split spans" in result.output
    clips = list((root / "work" / "T9" / "clips").glob("*.wav"))
    assert [p.name for p in clips] == ["0001_00-00.wav"]

    unresolved = runner.invoke(cli.app, ["resolve", "T9"])
    assert unresolved.exit_code == 1 and "nothing chosen" in unresolved.output

    md = root / "data" / "test-set" / "T9.adjudication.md"
    md.write_text(md.read_text(encoding="utf-8").replace("- [ ] B (assemblyai)", "- [x] B (assemblyai)"),
                  encoding="utf-8")
    resolved = runner.invoke(cli.app, ["resolve", "T9"])
    assert resolved.exit_code == 0, resolved.output
    assert (root / "data" / "test-set" / "T9.ref.txt").read_text(encoding="utf-8") == "then run /theme here\n"
    protocol = json.loads((root / "data" / "test-set" / "T9.ref.protocol.json").read_text(encoding="utf-8"))
    assert protocol["totals"]["spans_by_engine"]["assemblyai"] == 1
    assert "typed by the owner: 0 words" in resolved.output


def test_adjudicate_does_not_overwrite_the_owners_choices(project):
    root, (bb, b, c) = project
    args = ["adjudicate", "T9", "--backbone", str(bb), "--hyp", str(b), "--hyp", str(c)]
    assert runner.invoke(cli.app, args).exit_code == 0
    again = runner.invoke(cli.app, args)
    assert again.exit_code == 1 and "--force" in again.output
