"""
What the page may say about core messages and failed blocks (v1.1 session 4, after the owner's first
real run): no core line in the main view, a plain-language warning with the minutes left untranslated.
"""

import json
import re
from pathlib import Path

import pytest

from talk_autopsy.segments import Segment, Transcript
from talk_autopsy.web import jobs, notices, replay
from talk_autopsy.web import settings as web_settings
from talk_autopsy.web.app import STATIC_DIR
from talk_autopsy.web.transcript import TranscriptView
from tests.test_web_replay import request, wait

UNTRANSLATED = Path(__file__).parent / "fixtures" / "replay" / "synthetic-talk-untranslated.jsonl"
SRT_LINE = ("Create a Smart Contract Tutori.ru.srt + .ass: 191 cues; reading speed max 49.2 cps, "
            "27.2% above 17 cps, 5.1% above 25 cps; shorter than 1 s: 0, longer than 7 s: 3")


@pytest.mark.parametrize("code, text, message, params", [
    ("srt", SRT_LINE, "notice_subtitles_fast", {"lang": "ru", "cues": 191, "fast": 27}),
    ("srt", "talk.en.srt + .ass: 83 cues; reading speed max 14.0 cps, 3.0% above 17 cps, 0.0% above 25 cps",
     "notice_subtitles", {"lang": "en", "cues": 83, "fast": 3}),
    ("outputs", "mp4-burned: drawing the ru subtitles into the picture (H.264 re-encode)",
     "notice_burn_in", {}),
    ("outputs", "mp4-soft: adding subtitle tracks to x.mp4 (no re-encoding)", "notice_soft_tracks", {}),
    ("fallback", "AssemblyAI failed (TimeoutError); using gemini+align", "notice_fallback", {}),
    ("text_truncated", "Only the first part of the text is shown.", "notice_truncated", {}),
    ("outputs", "something new the core says", None, {}),
    ("srt", "not the usual shape", None, {}),
])
def test_core_messages_become_known_kinds_or_technical_details(code, text, message, params):
    assert notices.describe(code, text) == {"message": message, "params": params, "kind": "info"}


def test_every_message_and_failure_has_a_string_in_every_language():
    keys = {"notice_subtitles", "notice_subtitles_fast", "notice_burn_in", "notice_soft_tracks",
            "notice_fallback", "notice_display_error", "notice_truncated", "subs_which_target",
            "subs_which_en", "tech_details", "warn_untranslated", "warn_uncorrected", "warn_rerun",
            "warn_rerun_part"}
    keys |= {f"fail_{h}" for h in ("doctor", "youtube", "outputs", "cost_limit", "input", "log", "network")}
    for lang in ("en", "ru", "lt"):
        strings = json.loads((web_settings.STRINGS_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        assert keys <= set(strings), (lang, sorted(keys - set(strings)))
        assert "live_failed_blocks" not in strings  # the counter nobody understood is gone


def test_the_page_never_puts_a_core_line_in_the_main_view():
    steps_js = (STATIC_DIR / "steps.js").read_text(encoding="utf-8")
    started = steps_js.split("export function apply")[1].split("// the core's own description")[0]
    assert "event.detail" not in started
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    failed = app_js.split("async function showFailed")[1].split("\n}\n")[0]
    reason = failed.split('$("failed-reason").textContent')[1].split(";")[0]
    assert "event.reason" not in reason and '$("failed-tech-text").textContent = event.reason' in failed
    transcript_js = (STATIC_DIR / "transcript.js").read_text(encoding="utf-8")
    notice = transcript_js.split("export function notice")[1].split("\n}\n")[0]
    assert "els.notices.append" in notice and "event.text" not in notice.split("if (event.text)")[0]


def test_ranges_merge_where_blocks_touch():
    assert notices.merge_ranges([(300.0, 600.2), (0, 299.1), (900, 950)]) == [[0.0, 600.2], [900.0, 950.0]]
    assert notices.merge_ranges([]) == []


def test_failed_ranges_come_from_the_failed_blocks_segments():
    segments = [Segment(id=i, start=10.0 * i, end=10.0 * i + 8, text="x") for i in range(1, 7)]
    result = Transcript(segments=segments, language="ru", source="t",
                        meta={"translation": {"failed_blocks": [{"block": 1, "ids": [1, 2, 3]}]}})
    assert notices.failed_ranges(result, "translation") == [[10.0, 38.0]]
    assert notices.failed_ranges(result, "correction") == []
    assert notices.failed_ranges(None, "translation") == []


def test_the_live_summary_names_the_untranslated_minutes():
    view = TranscriptView()
    rows = [{"id": k, "start": 100.0 * (k - 1), "end": 100.0 * k - 1, "text": f"s{k}"} for k in range(1, 5)]
    view.apply({"type": "transcript", "source": "stt2", "replace": True, "rows": rows})
    view.apply({"type": "translated", "position": 1, "total": 2, "failed": True, "ids": [1, 2],
                "source_ids": {"1": [1], "2": [2, 3]}})
    summary = view.summary()
    assert summary["untranslated"] == [[0.0, 299.0]] and summary["uncorrected"] == []
    old = TranscriptView()  # a recording without segment ends: the next segment's start
    old.apply({"type": "transcript", "source": "stt2", "replace": True,
               "rows": [{"id": 1, "start": 0.0, "text": "a"}, {"id": 2, "start": 7.5, "text": "b"}]})
    old.apply({"type": "corrected", "position": 1, "total": 1, "failed": True, "ids": [1]})
    assert old.summary()["uncorrected"] == [[0.0, 7.5]]


def test_a_job_notice_carries_its_plain_message(tmp_path):
    job = jobs.Job(id="j", request=request(), preview=None, steps=[], started_wall="2026-11-12T19:00:00")
    event = job.add("notice", code="srt", text=SRT_LINE)
    assert event["message"] == "notice_subtitles_fast" and event["text"] == SRT_LINE


def test_the_untranslated_recording_finishes_with_the_range_and_the_marker_keeps_it(tmp_path):
    recording = replay.load(UNTRANSLATED)
    runner = replay.ReplayRunner(recording, speed=0, root=tmp_path, make_video=False)
    job = runner.start(request())
    events = wait(job)
    end = events[-1]
    assert end["type"] == "finished" and end["untranslated"] == [[0.0, 305.6]] and end["uncorrected"] == []
    assert job.view.summary()["untranslated"] == [[0.0, 305.6]]
    flagged = [e for e in events if e["type"] == "translated" and e["failed"]]
    assert len(flagged) == 1 and flagged[0]["position"] == 1
    from talk_autopsy.web import results
    info = results.describe(Path(end["folder"]))
    assert info["untranslated"] == [[0.0, 305.6]]
    assert not any(re.search(r"\.srt \+ \.ass", e.get("message") or "") for e in events)


REVERTED = Path(__file__).parent / "fixtures" / "replay" / "synthetic-talk-reverted.jsonl"


def test_a_reverted_segment_is_named_by_its_own_minutes_not_its_block(tmp_path):
    """pipeline-v3.4: a block accepted with one segment kept as it was; the warning names that segment."""
    recording = replay.load(REVERTED)
    runner = replay.ReplayRunner(recording, speed=0, root=tmp_path, make_video=False)
    job = runner.start(request())
    events = wait(job)
    end = events[-1]
    assert end["type"] == "finished"
    assert end["uncorrected"] == [[93.5, 101.6]] and end["untranslated"] == [[51.0, 59.1]]  # one each
    corrected = [e for e in events if e["type"] == "corrected"]
    assert [e.get("reverted") for e in corrected] == [[12], None] and not any(e["failed"] for e in corrected)
    window = job.view.window(0, 100)
    segments = {s["id"]: s for row in window["rows"] for s in row["segments"]}
    assert segments[12]["failed"] and not any(s["failed"] for i, s in segments.items() if i != 12)
    assert any(s["changed"] for s in segments.values())  # the block's other corrections are shown
    units = {u["id"]: u for row in window["rows"] for u in row["units"]}
    assert units[6]["failed"] and units[6]["text"] is None
    assert all(u["text"] for i, u in units.items() if i != 6)
    assert job.view.summary()["uncorrected"] == [[93.5, 101.6]]
    from talk_autopsy.web import results
    assert results.describe(Path(end["folder"]))["untranslated"] == [[51.0, 59.1]]


def test_left_ids_cover_failed_blocks_and_reverted_segments():
    segs = [Segment(id=k, start=10.0 * k, end=10.0 * k + 9, text="x") for k in range(1, 7)]
    t = Transcript(segments=segs, language="ru", source="x", meta={"translation": {
        "failed_blocks": [{"block": 1, "ids": [1, 2]}], "reverted_segments": [{"block": 2, "ids": [5]}]}})
    assert notices.left_ids(t, "translation") == {1, 2, 5}
    assert notices.failed_ranges(t, "translation") == [[10.0, 29.0], [50.0, 59.0]]
