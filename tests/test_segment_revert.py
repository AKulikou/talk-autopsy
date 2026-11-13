"""
One bad segment no longer costs a whole block (bug, the owner's third real video, 2026-11-13):
correction block 3 of a 26-minute talk failed twice on "glossary terms removed: MCP" and all
its five minutes kept the recognized text; on the owner's first video one translation segment
did the same to five minutes of translation.

Now, when both attempts are rejected only by per-segment checks, the block is accepted with
those segments reverted to their input (the recognized text, or the English source); the
other segments keep their corrections or translations. Structural problems (ids, edits for
segments outside the block) still fail the whole block. The requests are the same as before:
the retry happens as it did, the revert is applied only after both attempts. A result with a
reverted segment is not a finished result: the next run sends that block again, exactly as
before, and reuses every block whose request would be the same.

The check itself was also wrong for this case: "run MCP" (the speaker's words for the
`/mcp` command) corrected to "run /mcp" counted as removing the term MCP.
"""

import json
import time

import pytest

from talk_autopsy import correct, pipeline, translate
from talk_autopsy.correct import CorrectionAnswer, Edit
from talk_autopsy.correct import SegmentText as FixSegment
from talk_autopsy.glossary import parse_glossary
from talk_autopsy.segments import Segment, load_transcript, save_transcript
from talk_autopsy.translate import SegmentText, TranslationAnswer
from tests import synthetic_talk

MCP_GLOSSARY = """| term | keep | ru | notes |
|---|---|---|---|
| MCP | yes | | acronym; Model Context Protocol |
"""


@pytest.fixture
def talk(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "perf_counter", lambda: 1000.0)
    source = tmp_path / "talk.wav"
    source.write_bytes(b"not read: the speech steps are not selected")
    work = tmp_path / "work" / "talk"
    work.mkdir(parents=True)
    save_transcript(synthetic_talk.transcript(), work / "raw2.json")
    (work / "glossary.md").write_text(synthetic_talk.GLOSSARY_MD, encoding="utf-8", newline="\n")
    (work / "meta.json").write_text(json.dumps({"title": "Scheduling small jobs (invented)",
                                                "description": "", "chapters": []}) + "\n",
                                    encoding="utf-8", newline="\n")
    return pipeline.RunSettings(source=source, target_lang="ru", steps=["fix", "translate", "srt"],
                                work_root=tmp_path / "work")


def _requests(fake, kind) -> list[dict]:
    return [r for r in fake.requests if r["output_format"] is kind]


def _first_ids(fake, kind, verb) -> list[int]:
    return [synthetic_talk.requested(r["messages"][0]["content"], verb)[0][0] for r in _requests(fake, kind)]


def _correction_drops_a_term(kwargs):
    """Block 1's answer corrects as usual but drops "Brindle" from segment 2 (a real removal)."""
    answer = synthetic_talk.handler(kwargs)
    if kwargs["output_format"] is CorrectionAnswer:
        parsed = answer.parsed_output
        if parsed.segments[0].id == 1:
            text = parsed.segments[1].text.replace("Brindle queue", "queue")
            parsed.segments[1] = FixSegment(id=2, text=text)
    return answer


def _translation_leaves_unit_3(kwargs):
    """Translation block 1's answer leaves unit 3 in English; the rest is translated."""
    answer = synthetic_talk.handler(kwargs)
    if kwargs["output_format"] is TranslationAnswer:
        pairs = dict(synthetic_talk.requested(kwargs["messages"][0]["content"], "translate"))
        parsed = answer.parsed_output
        if parsed.segments[0].id == 1:
            parsed.segments[2] = SegmentText(id=3, text=pairs[3])
    return answer


def test_a_removed_term_reverts_only_its_segment(talk, fake_claude):
    fake = fake_claude(_correction_drops_a_term)
    pipeline.run(talk)
    assert _first_ids(fake, CorrectionAnswer, "correct") == [1, 1, 37, 73]  # retried as before
    fixed = load_transcript(talk.work_dir / "fixed.json")
    meta = fixed.meta["correction"]
    assert meta["failed_blocks"] == []
    assert [(r["block"], r["ids"]) for r in meta["reverted_segments"]] == [(1, [2])]
    assert "Brindle queue" in meta["reverted_segments"][0]["problems"]["2"][0]
    by_id = {s.id: s.text for s in fixed.segments}
    assert by_id[2] == synthetic_talk.SENTENCES[1]  # reverted to the recognized text
    assert by_id[1].startswith("Welcome everyone, today we look at how Quorvex")  # kept
    assert all(e["id"] != 2 for e in meta["edits"])


def _correction_borrows_words(kwargs):
    """Block 1's answer fixes segment 1 but also pulls words of segment 2 into it (a moved run)."""
    answer = synthetic_talk.handler(kwargs)
    if kwargs["output_format"] is CorrectionAnswer:
        parsed = answer.parsed_output
        if parsed.segments[0].id == 1:
            text = parsed.segments[0].text + " Every worker pulls from one"
            parsed.segments[0] = FixSegment(id=1, text=text)
    return answer


def test_a_reverted_segment_is_not_finished_and_only_changed_blocks_are_sent_again(talk, fake_claude):
    fake = fake_claude(_correction_borrows_words)
    pipeline.run(talk)
    first_block_1 = _requests(fake, CorrectionAnswer)[0]
    fixed = load_transcript(talk.work_dir / "fixed.json")
    assert [(r["block"], r["ids"]) for r in fixed.meta["correction"]["reverted_segments"]] == [(1, [1])]
    assert {s.id: s.text for s in fixed.segments}[1] == synthetic_talk.SENTENCES[0]  # "core vex" stays
    stamp = json.loads((talk.work_dir / "fixed.json.stamp.json").read_text(encoding="utf-8"))
    assert pipeline.INCOMPLETE in stamp

    fake = fake_claude(synthetic_talk.handler)
    pipeline.run(talk)
    assert _requests(fake, CorrectionAnswer) == [first_block_1]  # only block 1, the same request
    # segment 1 is corrected now, so translation block 1 is sent again; the requests of blocks 2
    # and 3 would be the same as before, so their accepted answers are reused
    assert _first_ids(fake, TranslationAnswer, "translate") == [1]
    fixed = load_transcript(talk.work_dir / "fixed.json")
    assert "reverted_segments" not in fixed.meta["correction"]
    first = {s.id: s.text for s in fixed.segments}[1]
    assert first.startswith("Welcome everyone, today we look at how Quorvex")
    assert "Quorvex" in {s.id: s.text for s in load_transcript(talk.work_dir / "ru.json").segments}[1]
    stamp = json.loads((talk.work_dir / "fixed.json.stamp.json").read_text(encoding="utf-8"))
    assert pipeline.INCOMPLETE not in stamp

    fake = fake_claude(synthetic_talk.handler)
    pipeline.run(talk)
    assert fake.requests == []  # complete now


def test_a_translation_segment_left_in_english_reverts_only_that_unit(talk, fake_claude):
    fake = fake_claude(_translation_leaves_unit_3)
    pipeline.run(talk)
    assert _first_ids(fake, TranslationAnswer, "translate")[:2] == [1, 1]  # retried as before
    ru = load_transcript(talk.work_dir / "ru.json")
    meta = ru.meta["translation"]
    assert meta["failed_blocks"] == []
    assert [(r["block"], r["ids"]) for r in meta["reverted_segments"]] == [(1, [3])]
    texts = {s.id: s.text for s in ru.segments}
    assert "перевод" not in texts[3]  # stays in English
    assert all("перевод" in t for i, t in texts.items() if i != 3)
    stamp = json.loads((talk.work_dir / "ru.json.stamp.json").read_text(encoding="utf-8"))
    assert pipeline.INCOMPLETE in stamp

    fake = fake_claude(synthetic_talk.handler)
    pipeline.run(talk)
    assert _first_ids(fake, TranslationAnswer, "translate") == [1]  # only that block again
    assert all("перевод" in s.text for s in load_transcript(talk.work_dir / "ru.json").segments)


def test_structural_problems_still_fail_the_whole_block():
    glossary = parse_glossary(synthetic_talk.GLOSSARY_MD)
    segments = synthetic_talk.segments()[:3]
    wrong_ids = CorrectionAnswer(segments=[FixSegment(id=s.id + 1, text=s.text) for s in segments], edits=[])
    assert correct.revert_segments(segments, glossary, wrong_ids) is None
    stray_edit = CorrectionAnswer(segments=[FixSegment(id=s.id, text=s.text) for s in segments],
                                  edits=[Edit(id=99, before="a", after="b", reason="x")])
    assert correct.revert_segments(segments, glossary, stray_edit) is None
    answer = TranslationAnswer(segments=[SegmentText(id=s.id + 1, text="перевод") for s in segments])
    assert translate.revert_segments(segments, glossary, "ru", answer) is None


def test_validation_messages_are_unchanged():
    """The same answers get the same problems, word for word (cached answers keep their verdicts)."""
    glossary = parse_glossary(synthetic_talk.GLOSSARY_MD)
    segments = synthetic_talk.segments()[:3]
    answer = CorrectionAnswer(segments=[FixSegment(id=1, text=""),
                                        FixSegment(id=2, text=segments[1].text.replace("Brindle queue", "q")),
                                        FixSegment(id=3, text=segments[2].text)], edits=[])
    assert correct.validate_block(segments, glossary, answer) == [
        "segment 1 is empty", "glossary terms removed: Brindle queue",
        "text length changed from 193 to 112 characters (over 10%)"]


def test_a_term_written_as_a_command_is_not_removed():
    """ "run MCP" -> "run /mcp": the speaker named the /mcp command; the term is still there."""
    glossary = parse_glossary(MCP_GLOSSARY)
    said = "So run MCP, switch off whatever you're not using in that session."
    segments = [Segment(id=1, start=0.0, end=8.0, text=said)]
    for written in ("So run /mcp, switch off whatever you're not using in that session.",
                    "So run `/mcp`, switch off whatever you're not using in that session."):
        answer = CorrectionAnswer(segments=[FixSegment(id=1, text=written)],
                                  edits=[Edit(id=1, before="run MCP", after=written.split(",")[0][3:],
                                              reason="command name")])
        assert correct.validate_block(segments, glossary, answer) == []
    dropped = CorrectionAnswer(segments=[FixSegment(id=1, text=said.replace("MCP", "it"))], edits=[])
    assert correct.validate_block(segments, glossary, dropped) == ["glossary terms removed: MCP"]
