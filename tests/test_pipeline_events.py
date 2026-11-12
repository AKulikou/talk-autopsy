"""
pipeline-v3.1 observability: block events with text, numeric estimate and step
data, and that a failing display never affects a step. The golden tests
(test_golden_requests.py) show the same runs send the same requests.
"""

import json

import pytest

from talk_autopsy import pipeline
from talk_autopsy.core import structured_call
from talk_autopsy.correct import CorrectionAnswer
from talk_autopsy.correct import SegmentText as FixSegment
from talk_autopsy.segments import load_transcript, save_transcript
from tests import synthetic_talk
from tests.conftest import response


def prepared(tmp_path):
    source = tmp_path / "talk.wav"
    source.write_bytes(b"not read")
    work = tmp_path / "work" / "talk"
    work.mkdir(parents=True)
    save_transcript(synthetic_talk.transcript(), work / "raw2.json")
    (work / "glossary.md").write_text(synthetic_talk.GLOSSARY_MD, encoding="utf-8")
    (work / "meta.json").write_text(json.dumps({"title": "Invented", "description": "", "chapters": []}),
                                    encoding="utf-8")
    return pipeline.RunSettings(source=source, target_lang="ru", steps=["fix", "translate", "srt"],
                                work_root=tmp_path / "work")


def blocks_of(events, step):
    return [e for e in events if e.stage == step and e.event == "block-done"]


def test_fix_blocks_carry_their_segments_and_edits(tmp_path, fake_claude):
    fake_claude(synthetic_talk.handler)
    events = []
    pipeline.run(prepared(tmp_path), progress=events.append)
    fix = blocks_of(events, "fix")
    assert [e.detail for e in fix] == ["block 1/3", "block 2/3", "block 3/3"]
    assert all(isinstance(e, pipeline.PipelineEvent) for e in fix)
    first = fix[0].data
    assert first["position"] == 1 and first["total"] == 3 and first["failed"] is False
    assert first["ids"] == [s["id"] for s in first["segments"]]
    assert {"id": 1, "before": "core vex", "after": "Quorvex"} in first["edits"]
    assert first["segments"][0]["text"].startswith("Welcome everyone, today we look at how Quorvex")
    # every segment of the talk is in exactly one block event
    ids = [i for e in fix for i in e.data["ids"]]
    assert ids == list(range(1, synthetic_talk.SEGMENT_COUNT + 1))


def test_translate_blocks_carry_units_with_source_and_segment_ids(tmp_path, fake_claude):
    fake_claude(synthetic_talk.handler)
    events = []
    settings = prepared(tmp_path)
    pipeline.run(settings, progress=events.append)
    blocks = blocks_of(events, "translate")
    assert blocks and all(not e.data["failed"] for e in blocks)
    units = [u for e in blocks for u in e.data["units"]]
    final = load_transcript(settings.work_dir / "ru.json")
    assert [u["id"] for u in units] == final.ids()
    assert [u["text"] for u in units] == [s.text for s in final.segments]
    by_id = {s.id: s for s in final.segments}
    for e in blocks:
        for u in e.data["units"]:
            assert e.data["source_ids"][str(u["id"])] == by_id[u["id"]].source_ids
            assert u["source"]  # the English unit text
    # a unit spanning two segments (the sentence ends in the next segment)
    assert any(len(ids) == 2 for e in blocks for ids in e.data["source_ids"].values())


def test_estimate_and_step_events_carry_numbers(tmp_path, fake_claude):
    fake_claude(synthetic_talk.handler)
    events = []
    report = pipeline.run(prepared(tmp_path), progress=events.append)
    estimates = [e for e in events if e.event == "estimate"]
    assert [e.data["part"] for e in estimates] == ["fix", "translate"]
    assert all(e.data["usd"] > 0 and e.data["blocks"] == 3 and e.data["input_tokens"] > 0 for e in estimates)
    done = {e.stage: e.data for e in events if e.event == "step-done"}
    by_step = {s.step: s for s in report.steps}
    for step in ("fix", "translate"):
        assert done[step]["cost_usd"] == by_step[step].cost_usd > 0
        assert done[step]["tokens"]["input"] > 0 and done[step]["tokens"]["output"] > 0
        assert done[step]["seconds"] == by_step[step].seconds
    assert done["srt"]["tokens"] is None and done["srt"]["cost_usd"] == 0.0


def test_a_failed_block_is_reported_as_failed_without_text(tmp_path, fake_claude):
    def handler(kwargs):
        user = kwargs["messages"][0]["content"]
        if kwargs["output_format"] is CorrectionAnswer and "[30]" in user.split("Segments to correct:")[1]:
            return response(CorrectionAnswer(segments=[FixSegment(id=999, text="wrong")], edits=[]))
        return synthetic_talk.handler(kwargs)
    fake_claude(handler)
    events = []
    pipeline.run(prepared(tmp_path), progress=events.append)
    fix = blocks_of(events, "fix")
    failed = [e.data for e in fix if e.data["failed"]]
    assert len(failed) == 1 and "segments" not in failed[0] and 30 in failed[0]["ids"]
    assert [e.data["failed"] for e in fix].count(False) == 2


def test_a_raising_progress_callback_never_stops_the_run(tmp_path, fake_claude):
    fake_claude(synthetic_talk.handler)
    calls = []

    def broken(event):
        calls.append(event)
        raise RuntimeError("display broke")
    report = pipeline.run(prepared(tmp_path), progress=broken)
    assert [s.step for s in report.steps] == ["fix", "translate", "srt"]
    assert len(calls) > 10


def test_a_raising_observer_never_reaches_the_call(fake_claude):
    fake = fake_claude(lambda kwargs: response(CorrectionAnswer(segments=[], edits=[])))

    def broken(label, result):
        raise RuntimeError("observer broke")
    token = structured_call.result_observer.set(broken)
    try:
        answer = structured_call.request_structured(
            fake, label="t", model="m", max_tokens=10, system="", user="u", output_format=CorrectionAnswer,
            validate=lambda a: [])
    finally:
        structured_call.result_observer.reset(token)
    assert answer.result.segments == []


def test_the_observer_sees_only_accepted_results(fake_claude):
    answers = iter([CorrectionAnswer(segments=[FixSegment(id=1, text="bad")], edits=[]),
                    CorrectionAnswer(segments=[FixSegment(id=1, text="good")], edits=[])])
    fake = fake_claude(lambda kwargs: response(next(answers)))
    seen = []
    token = structured_call.result_observer.set(lambda label, result: seen.append(result))
    try:
        structured_call.request_structured(
            fake, label="t", model="m", max_tokens=10, system="", user="u", output_format=CorrectionAnswer,
            validate=lambda a: ["bad"] if a.segments[0].text == "bad" else [])
    finally:
        structured_call.result_observer.reset(token)
    assert [r.segments[0].text for r in seen] == ["good"]
    assert structured_call.result_observer.get() is None


@pytest.mark.parametrize("event", [pipeline.PipelineEvent("fix", "block-done", 0, 3, 0, 0, 1.0, "pipeline",
                                                          "block 1/3", data={"x": 1})])
def test_a_pipeline_event_prints_like_a_progress_event(event):
    assert event.line() == pipeline.ProgressEvent(*[getattr(event, f) for f in (
        "stage", "event", "chunk", "chunks", "audio_seconds_done", "audio_seconds_total", "elapsed", "engine",
        "detail")]).line()
