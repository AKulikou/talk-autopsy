"""
Replay mode (web/replay.py) with the committed synthetic recordings
(tests/fixtures/replay/, made by tests/make_replay_fixture.py): the normal job
and SSE path, no pipeline, no network, no cost.
"""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from talk_autopsy.web import jobs, replay
from talk_autopsy.web.app import TOKEN_HEADER, create_app
from talk_autopsy.web.jobs import GateError, JobRequest
from tests.test_web_app import BASE, PORT, TOKEN, loopback_only, sse  # noqa: F401  (fixture)

FIXTURES = Path(__file__).parent / "fixtures" / "replay"
FINISHED, FAILED = FIXTURES / "synthetic-talk.jsonl", FIXTURES / "synthetic-talk-failed.jsonl"
URL = "https://www.youtube.com/watch?v=abc123DEF45"


def request(url: str = URL) -> JobRequest:
    return JobRequest(mode="url", url=url, profile="tv", domain="none", target_lang="ru")


def wait(job, seconds: float = 10.0):
    deadline = time.monotonic() + seconds
    while job.state == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    return job.events_after(0)[0]


def test_the_committed_recordings_load():
    finished, failed = replay.load(FINISHED), replay.load(FAILED)
    assert finished.title == "Scheduling tiny jobs (invented talk)" and finished.duration_s == 340
    assert finished.steps[0] == "download" and finished.events[-1]["type"] == "finished"
    assert failed.events[-1]["type"] == "failed" and failed.events[-1]["step"] == "translate"
    types = {e["type"] for e in finished.events}
    assert {"transcript", "corrected", "translated", "step_progress", "step_finished"} <= types


@pytest.mark.parametrize("text, message", [
    ("", "no recording header"), ('{"type": "header", "format": 1}', "no events"),
    ('{"type": "header", "format": 1}\n{"type": "notice", "t": 1}', "does not end"),
    ("not json", "cannot be read"), ('{"type": "header", "format": 2}\n', "no recording header")])
def test_a_bad_recording_is_refused(tmp_path, text, message):
    path = tmp_path / "r.jsonl"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(replay.RecordingError, match=message):
        replay.load(path)


def test_a_replay_plays_every_event_and_builds_a_demo_result(tmp_path):
    recording = replay.load(FINISHED)
    runner = replay.ReplayRunner(recording, speed=0, root=tmp_path, make_video=False)
    job = runner.start(request())
    events = wait(job)
    assert job.state == "finished" and job.replay and job.summary(3.0)["replay"]
    assert [e["type"] for e in events] == [e["type"] for e in recording.events]
    summary = job.view.summary()
    assert summary["edits"] == 8 and summary["segments"] == 40 and summary["units"] == summary["segments"] - 6
    folder = Path(events[-1]["folder"])
    assert folder.is_relative_to(tmp_path / "output")
    stem = "Scheduling tiny jobs (invented talk)"
    target = (folder / f"{stem}.srt").read_text(encoding="utf-8")
    english = (folder / f"{stem}.en.srt").read_text(encoding="utf-8")
    assert "Quorvex" in target and "Сегодня я хочу показать" in target and "flinter mesh" in english
    assert "core vex" not in english  # the corrected text, not the recognized one
    assert json.loads((folder / jobs.RESULT_MARKER).read_text(encoding="utf-8"))["replay"] is True
    assert runner.active_id is None  # the next replay can start


def test_a_failed_recording_ends_failed(tmp_path):
    runner = replay.ReplayRunner(replay.load(FAILED), speed=0, root=tmp_path, make_video=False)
    job = runner.start(request())
    events = wait(job)
    assert job.state == "failed" and events[-1]["hint"] == "log" and events[-1]["step"] == "translate"
    assert job.view.summary()["translation_blocks"] == 1


def test_natural_speed_waits_for_the_recorded_times(tmp_path):
    waits = []
    runner = replay.ReplayRunner(replay.load(FAILED), speed=10, root=tmp_path, make_video=False,
                                 sleep=waits.append)
    job = runner.start(request())
    wait(job)
    # the fake sleep does not sleep, so each wait is the whole recorded time so far, over the speed
    assert waits and max(waits) == pytest.approx(replay.load(FAILED).events[-1]["t"] / 10, abs=0.5)


def test_the_estimate_needs_no_network_and_long_shows_the_confirmation(tmp_path):
    runner = replay.ReplayRunner(replay.load(FINISHED), speed=0, root=tmp_path, make_video=False)
    short = runner.preview(request())
    assert short.duration_s == 340 and not short.needs_confirmation(runner.limit_usd)
    long = runner.preview(request("https://www.youtube.com/watch?v=longlong123"))
    assert long.duration_s == replay.LONG_SECONDS and long.needs_confirmation(runner.limit_usd)
    with pytest.raises(GateError):
        runner.start(request("https://www.youtube.com/watch?v=longlong123"))
    job = runner.start(request("https://www.youtube.com/watch?v=longlong123").model_copy(
        update={"confirmed_cost_usd": long.amount_usd}))
    assert wait(job)[-1]["type"] == "finished"


def test_the_page_gets_light_text_events_and_reads_rows(tmp_path, loopback_only):  # noqa: F811
    runner = replay.ReplayRunner(replay.load(FINISHED), speed=0, root=tmp_path, make_video=False)
    app = create_app(PORT, runner=runner, token=TOKEN, doctor=lambda: [], local_settings=tmp_path / "w.json",
                     heartbeat_s=0.0)
    with TestClient(app, base_url=BASE) as client:
        client.headers[TOKEN_HEADER] = TOKEN
        payload = {"mode": "url", "url": URL, "target_lang": "ru", "profile": "tv", "domain": "none"}
        started = client.post("/api/jobs", json=payload)
        assert started.status_code == 202 and started.json()["job"]["replay"] is True
        job_id = started.json()["job"]["id"]
        events, _ = sse(client, job_id)
        text = [e for e in events if e["type"] == "text_changed"]
        kinds = ["transcript", "transcript", "corrected", "corrected", "translated", "translated"]
        assert [e["kind"] for e in text] == kinds
        assert all("rows" not in e and "segments" not in e and "units" not in e for e in text)
        assert max(len(json.dumps(e)) for e in events) < 2000  # no event carries the talk's text
        window = client.get(f"/api/jobs/{job_id}/transcript", params={"start": 0, "count": 10}).json()
        assert window["rows"][1]["segments"][0]["changed"]  # "core vex" -> "Quorvex"
        assert {"k": "ins", "t": "Quorvex"} in window["rows"][1]["segments"][0]["pieces"]
        assert window["rows"][1]["units"][0]["text"].startswith("Сегодня")
        follow = client.get(f"/api/jobs/{job_id}/transcript").json()
        assert follow["end"] == 40 and not follow["more_after"]
        assert client.get("/api/jobs/nope/transcript").status_code == 404


def test_a_recorded_job_replays(tmp_path):
    from tests.web_fakes import FakeCore
    media = tmp_path / "talk.mp4"
    media.write_bytes(b"x")
    runner = jobs.JobRunner(core=FakeCore(tmp_path), history_file=tmp_path / "h.json", tick_s=3600,
                            record_dir=tmp_path / "rec")
    file_request = JobRequest(mode="file", path=str(media), profile="tv", domain="none", target_lang="lt")
    job = runner.start(file_request)
    events = wait(job)
    files = list((tmp_path / "rec").glob("*.jsonl"))
    assert len(files) == 1 and files[0].name.endswith(f"-{job.id}.jsonl")
    recording = replay.load(files[0])
    assert recording.summary["id"] == job.id and recording.steps == job.steps
    assert [e["id"] for e in recording.events] == [e["id"] for e in events]
    again = replay.ReplayRunner(recording, speed=0, root=tmp_path / "replay", make_video=False)
    played = wait(again.start(file_request))
    assert [e["type"] for e in played] == [e["type"] for e in events]


def test_old_recordings_are_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "KEEP_RECORDINGS", 3)
    folder = tmp_path / "rec"
    folder.mkdir()
    for n in range(5):
        (folder / f"2026111{n}-old.jsonl").write_text("{}", encoding="utf-8")
    job = jobs.Job(id="j", request=request(), preview=None, steps=[], started_wall="2026-11-12T19:00:00")
    job.start_recording(folder, {"summary": {}})
    job.add("notice", code="x", text="y")
    assert len(list(folder.glob("*.jsonl"))) == 3
