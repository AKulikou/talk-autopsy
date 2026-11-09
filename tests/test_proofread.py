"""Draft export and reference import: the proofreading round trip."""

from typer.testing import CliRunner

from talk_autopsy import cli, metrics
from talk_autopsy.proofread import draft_text, reference_from_draft
from talk_autopsy.segments import Segment, Transcript, save_transcript

runner = CliRunner()


def _transcript(*timed_texts):
    segments = [Segment(id=i, start=start, end=start + 1.0, text=text)
                for i, (start, text) in enumerate(timed_texts, start=1)]
    return Transcript(segments=segments, language="en", source="test")


TRANSCRIPT = _transcript(
    (1.2, " Hello everyone."),
    (14.9, " Today:  Claude Code,\n kubectl and npm install -g."),
    (59.99, " Last words of minute zero."),
    (60.0, " Minute one starts."),
    (185.5, " Minute three, after a gap."),
    (4503.0, " Seventy-five minutes in."),
)


def test_draft_has_one_line_per_segment_and_a_blank_line_per_minute():
    assert draft_text(TRANSCRIPT) == (
        "[00:01] Hello everyone.\n"
        "[00:14] Today: Claude Code, kubectl and npm install -g.\n"
        "[00:59] Last words of minute zero.\n"
        "\n"
        "[01:00] Minute one starts.\n"
        "\n"
        "[03:05] Minute three, after a gap.\n"
        "\n"
        "[75:03] Seventy-five minutes in.\n"
    )


def test_reference_strips_prefixes_and_joins_paragraphs():
    draft = (
        "[00:01] Hello  everyone.\n"
        "[00:14] Today we use kubectl. [inaudible]\n"
        "\n"
        "\n"
        "[01:00]   Minute one.\n"
        "[01:07]\n"  # the owner deleted a hallucinated segment's text
        "a line the owner added\n"
    )
    assert reference_from_draft(draft) == (
        "Hello everyone. Today we use kubectl. [inaudible]\n"
        "\n"
        "Minute one. a line the owner added\n"
    )


def test_round_trip_keeps_every_word_in_order():
    reference = reference_from_draft(draft_text(TRANSCRIPT))
    expected = metrics.tokenize(" ".join(s.text for s in TRANSCRIPT.segments), lowercase=False)
    assert metrics.tokenize(reference, lowercase=False) == expected
    assert reference.count("\n\n") == 3  # four minute blocks


def test_empty_transcript_gives_an_empty_draft():
    assert draft_text(_transcript()) == ""
    assert reference_from_draft("\n\n[00:00]\n") == ""


def test_cli_round_trip_and_overwrite_protection(tmp_path):
    raw = save_transcript(TRANSCRIPT, tmp_path / "raw.json")
    draft = tmp_path / "T9.draft.txt"
    ref = tmp_path / "T9.ref.txt"
    result = runner.invoke(cli.app, ["draft", str(raw), "--out", str(draft)])
    assert result.exit_code == 0, result.output
    assert "6 lines" in result.output

    # A draft the owner may have edited is not overwritten without --force.
    draft.write_text("[00:01] Hello, edited.\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["draft", str(raw), "--out", str(draft)])
    assert result.exit_code == 1 and "--force" in result.output
    assert draft.read_text(encoding="utf-8") == "[00:01] Hello, edited.\n"

    result = runner.invoke(cli.app, ["ref-from-draft", str(draft), "--out", str(ref)])
    assert result.exit_code == 0, result.output
    assert ref.read_text(encoding="utf-8") == "Hello, edited.\n"
    assert runner.invoke(cli.app, ["ref-from-draft", str(draft), "--out", str(ref)]).exit_code == 1
    assert runner.invoke(cli.app, ["ref-from-draft", str(draft), "--out", str(ref), "--force"]).exit_code == 0


def test_draft_saved_with_a_bom_still_converts(tmp_path):
    draft = tmp_path / "d.txt"
    draft.write_bytes("[00:01] Hello.\n".encode("utf-8-sig"))
    ref = tmp_path / "r.txt"
    assert runner.invoke(cli.app, ["ref-from-draft", str(draft), "--out", str(ref)]).exit_code == 0
    assert ref.read_bytes() == b"Hello.\n"
