"""
A block that failed validation is not a finished result (bug found on a real video, 2026-11-12):
the translation of a video whose first block failed was stamped as up to date, so running the
video again kept the English block for ever. Now the failed block is sent again on the next run,
the blocks that were accepted come from the earlier result (no request), and only a complete
translation is stamped as finished. A failed correction block makes the correction step run again.
"""

import json
import time

import pytest

from talk_autopsy import pipeline
from talk_autopsy.correct import CorrectionAnswer
from talk_autopsy.correct import SegmentText as FixSegment
from talk_autopsy.segments import load_transcript, save_transcript
from talk_autopsy.translate import SegmentText, TranslationAnswer
from tests import synthetic_talk
from tests.conftest import response


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


def _translation_requests(fake) -> list[list[int]]:
    """The unit ids of every translation request, in order."""
    return [[i for i, _ in synthetic_talk.requested(r["messages"][0]["content"], "translate")]
            for r in fake.requests if r["output_format"] is TranslationAnswer]


def _first_block_left_in_english(kwargs):
    """The fake model, except that the block holding unit 1 comes back untranslated."""
    if kwargs["output_format"] is TranslationAnswer:
        pairs = synthetic_talk.requested(kwargs["messages"][0]["content"], "translate")
        if pairs[0][0] == 1:
            return response(TranslationAnswer(segments=[SegmentText(id=i, text=t) for i, t in pairs]))
    return synthetic_talk.handler(kwargs)


def test_a_failed_translation_block_is_retried_and_the_good_blocks_are_reused(talk, fake_claude):
    fake = fake_claude(_first_block_left_in_english)
    pipeline.run(talk)
    ru = talk.work_dir / "ru.json"
    first = load_transcript(ru)
    failed = first.meta["translation"]["failed_blocks"]
    assert [b["block"] for b in failed] == [1]
    assert len(_translation_requests(fake)) == 4  # block 1 twice (rejected), blocks 2 and 3 once
    good = {s.id: s.text for s in first.segments if s.id not in failed[0]["ids"]}

    fake = fake_claude(synthetic_talk.handler)
    report = pipeline.run(talk)
    again = _translation_requests(fake)
    assert len(again) == 1 and again[0][0] == 1  # only the failed block is sent again
    assert not any(r["output_format"] is CorrectionAnswer for r in fake.requests)  # correction cached
    second = load_transcript(ru)
    assert second.meta["translation"]["failed_blocks"] == []
    assert {s.id: s.text for s in second.segments if s.id in good} == good  # accepted blocks kept
    assert all("перевод" in s.text for s in second.segments)
    translate_step = next(s for s in report.steps if s.step == "translate")
    assert not translate_step.skipped and translate_step.cost_usd > 0

    fake = fake_claude(synthetic_talk.handler)
    report = pipeline.run(talk)  # now complete: nothing is sent
    assert fake.requests == []
    assert next(s for s in report.steps if s.step == "translate").skipped


def test_the_retried_block_is_sent_exactly_as_the_first_time(talk, fake_claude):
    fake = fake_claude(_first_block_left_in_english)
    pipeline.run(talk)
    first = next(r for r in fake.requests if r["output_format"] is TranslationAnswer)
    fake = fake_claude(synthetic_talk.handler)
    pipeline.run(talk)
    assert fake.requests[0] == first  # same prefix, context and segments: the same request


def test_a_failed_translation_block_is_never_stamped_as_finished(talk, fake_claude):
    fake_claude(_first_block_left_in_english)
    pipeline.run(talk)
    fake = fake_claude(_first_block_left_in_english)  # the model still fails on block 1
    pipeline.run(talk)
    assert len(_translation_requests(fake)) == 2  # block 1 again, twice; blocks 2 and 3 reused
    assert load_transcript(talk.work_dir / "ru.json").meta["translation"]["failed_blocks"]


def _first_correction_block_broken(kwargs):
    if kwargs["output_format"] is CorrectionAnswer:
        pairs = synthetic_talk.requested(kwargs["messages"][0]["content"], "correct")
        if pairs[0][0] == 1:  # every segment emptied: rejected
            return response(CorrectionAnswer(segments=[FixSegment(id=i, text="") for i, _ in pairs],
                                             edits=[]))
    return synthetic_talk.handler(kwargs)


def test_a_failed_correction_block_is_not_stamped_as_finished(talk, fake_claude):
    fake_claude(_first_correction_block_broken)
    pipeline.run(talk)
    assert load_transcript(talk.work_dir / "fixed.json").meta["correction"]["failed_blocks"]
    fake = fake_claude(synthetic_talk.handler)
    pipeline.run(talk)
    assert any(r["output_format"] is CorrectionAnswer for r in fake.requests)  # correction runs again
    assert load_transcript(talk.work_dir / "fixed.json").meta["correction"]["failed_blocks"] == []
