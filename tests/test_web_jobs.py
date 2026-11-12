"""
The web job runner: request validation, the cost gate, one job at a time, the
event list (order, ids, replay, caps), failures, and that no secret value can
reach an event. A fake core stands in for the pipeline (tests/web_fakes.py).
"""

import json
import os
import threading
import time

import pytest
from pydantic import ValidationError

from talk_autopsy import pipeline
from talk_autopsy.config import ConfigurationError
from talk_autopsy.web import jobs
from talk_autopsy.web.jobs import BusyError, GateError, JobRequest, JobRunner
from talk_autopsy.web.sources import InputError, normalize_youtube_url
from tests.web_fakes import CACHED_RUN, FULL_RUN, FakeCore, pe

URL = "https://www.youtube.com/watch?v=abc123"


@pytest.fixture
def media(tmp_path):
    path = tmp_path / "talk.mp4"
    path.write_bytes(b"not really a video")
    return path


def request(**kw) -> JobRequest:
    data = {"mode": "url", "url": URL, "target_lang": "lt", **kw}
    return JobRequest.model_validate(data)


def runner(tmp_path, **core_kw) -> tuple[JobRunner, FakeCore]:
    core = FakeCore(tmp_path, **core_kw)
    return JobRunner(core=core, history_file=tmp_path / "history.json", tick_s=3600), core


def wait(job, timeout=10.0):
    end = time.monotonic() + timeout
    while job.state == "running":
        assert time.monotonic() < end, "job did not end"
        time.sleep(0.01)
    return job.events_after(0)[0]


def types(events):
    return [e["type"] for e in events]


def code_of(excinfo) -> str:
    error = jobs.input_error(excinfo.value)
    assert error is not None, excinfo.value
    return error.code


# --- request validation --------------------------------------------------------------------


def test_url_mode_is_normalized():
    r = request(url="  https://youtu.be/abc123?t=10  ")
    assert r.url == URL and r.source == URL and r.path is None


@pytest.mark.parametrize("url", [URL + "&list=PL123&index=2", "youtube.com/watch?v=abc123",
                                 "https://m.youtube.com/watch?v=abc123", "https://www.youtube.com/shorts/abc123"])
def test_video_links_name_one_video(url):
    assert normalize_youtube_url(url) == URL


def test_file_mode(media):
    r = request(mode="file", url=None, path=f"  {media}  ")
    assert r.path == str(media) and r.url is None


@pytest.mark.parametrize("kw,code", [
    ({"path": "C:/x.mp4"}, "both_given"),
    ({"url": None}, "neither_given"),
    ({"url": "  "}, "neither_given"),
    ({"url": "https://example.com/watch?v=abc123"}, "bad_url"),
    ({"url": "https://www.youtube.com/playlist?list=PL123"}, "playlist"),
    ({"target_lang": "xx"}, "bad_language"),
])
def test_url_mode_rejections(kw, code):
    with pytest.raises(ValidationError) as e:
        request(**kw)
    assert code_of(e) == code


def test_file_mode_rejections(tmp_path, media):
    folder = tmp_path / "folder.mp4"
    folder.mkdir()
    text = tmp_path / "notes.txt"
    text.write_text("x", encoding="utf-8")
    cases = [({"path": str(media), "url": URL}, "both_given"),
             ({"path": None, "url": None}, "neither_given"),
             ({"path": "talk.mp4", "url": None}, "not_absolute"),
             ({"path": str(tmp_path / "missing.mp4"), "url": None}, "no_such_file"),
             ({"path": str(folder), "url": None}, "not_a_file"),
             ({"path": str(text), "url": None}, "bad_extension")]
    for kw, code in cases:
        with pytest.raises(ValidationError) as e:
            request(mode="file", **kw)
        assert code_of(e) == code, kw


def test_unknown_fields_are_refused():
    with pytest.raises(ValidationError):
        request(max_cost_usd=100)


# --- preview -------------------------------------------------------------------------------


def test_preview_of_a_link(tmp_path):
    r, core = runner(tmp_path)
    preview = r.preview(request())
    assert preview.title == "A talk" and preview.duration_s == 600
    assert preview.formats == ["srt", "mp4-burned"]
    assert preview.notes == ["quality_not_measured"]  # lt is not measured
    assert preview.time.available and preview.time.first_run_rough
    d = preview.as_dict(3.0)
    assert d["cost"]["total_usd"] == pytest.approx(round(600 / 3600 * 0.82, 2) + round(600 / 3600 * 0.47, 2))
    assert d["needs_confirmation"] is False


def test_russian_has_no_quality_flag(tmp_path):
    r, _ = runner(tmp_path)
    assert r.preview(request(target_lang="ru")).notes == []


def test_a_playlist_found_at_estimate_time_is_refused(tmp_path):
    r, _ = runner(tmp_path, playlist=True)
    with pytest.raises(InputError) as e:
        r.preview(request())
    assert e.value.code == "playlist" and "launcher" in str(e.value)


def test_audio_only_file_gets_no_video_outputs(tmp_path):
    audio = tmp_path / "talk.mp3"
    audio.write_bytes(b"x")
    r, _ = runner(tmp_path)
    preview = r.preview(request(mode="file", url=None, path=str(audio), profile="all"))
    assert preview.formats == ["srt", "ass", "bilingual"] and "audio_only" in preview.notes


def test_profiles_map_to_existing_formats(tmp_path, media):
    r, _ = runner(tmp_path)
    laptop = r.preview(request(mode="file", url=None, path=str(media), profile="laptop"))
    assert laptop.formats == ["srt", "ass", "bilingual", "mp4-soft"]
    everything = r.preview(request(mode="file", url=None, path=str(media), profile="all"))
    assert everything.formats == ["srt", "ass", "bilingual", "mp4-soft", "mp4-burned"]


# --- happy paths --------------------------------------------------------------------------


def test_file_job_events_in_order(tmp_path, media):
    r, core = runner(tmp_path)
    job = r.start(request(mode="file", url=None, path=str(media)))
    events = wait(job)
    assert job.state == "finished"
    assert [e["id"] for e in events] == list(range(1, len(events) + 1))
    assert all(a["t"] <= b["t"] for a, b in zip(events, events[1:], strict=False))
    assert events[0]["type"] == "estimate_updated" and events[-1]["type"] == "finished"
    assert "download" not in [e.get("step") for e in events]
    started = [e["step"] for e in events if e["type"] == "step_started"]
    assert started[:2] == ["stt", "stt"]  # audio extraction, then recognition, both under stt
    finished = {e["step"]: e for e in events if e["type"] == "step_finished"}
    assert list(finished) == ["stt", "glossary", "stt2", "align", "fix", "translate", "srt", "outputs"]
    assert finished["stt"]["stt_cost_usd"] == 0.04 and finished["glossary"]["cost_usd"] == 0.009
    assert finished["fix"]["tokens"] == {"input": 9000, "output": 4000, "cache_write": 0, "cache_read": 0}
    assert finished["glossary"]["tokens"] is None  # the core does not report them for this step
    progress = [(e["step"], e["position"], e["total"]) for e in events if e["type"] == "step_progress"]
    # burn-in positions are throttled: the first and the last of three reported within 0.5 s
    assert progress == [("fix", 1, 2), ("fix", 2, 2), ("translate", 1, 1), ("outputs", 100, 600),
                        ("outputs", 600, 600)]
    # the fake core gives plain events (no block text): every text is read from the step's file
    texts = [e for e in events if e["type"] in ("transcript", "corrected", "translated")]
    assert [(e["type"], e.get("source")) for e in texts] == [("transcript", "stt"), ("transcript", "stt2"),
                                                              ("corrected", None), ("translated", None)]
    assert [r["text"] for r in texts[0]["rows"]] == ["Hello agents.", "We use cloud code."]
    assert texts[0]["replace"]
    assert texts[2]["edits"] == [{"id": 2, "before": "cloud code", "after": "Claude Code"}]
    assert texts[3]["units"] == [{"id": 1, "text": "Sveiki.", "source": "", "source_ids": [1, 2]}]
    view = job.view.window(0, 10)
    assert view["summary"]["edits"] == 1 and view["summary"]["units"] == 1
    assert [len(row["segments"]) for row in view["rows"]] == [2]  # one unit spans both segments
    claude = [e["claude_usd"] for e in events if e["type"] == "estimate_updated" and "claude_usd" in e]
    assert claude == [0.01, 0.06, 0.14]
    end = events[-1]
    assert end["folder"] == str(tmp_path / "output" / "talk")
    assert [f["kind"] for f in end["files"]] == ["subs_target", "tv"]
    assert [(f["name"], f["purpose"]) for f in end["files"]] == [("talk.srt", "subtitles"),
                                                                  ("talk.lt.mp4", "tv")]
    assert end["cost_usd"] == pytest.approx(0.119) and end["stt_cost_usd"] == pytest.approx(0.08)
    assert core.calls[:2] == ["probe", "check_keys"] and core.done_marked == []
    assert core.settings.target_lang == "lt" and core.settings.max_cost_usd == 3.0


def test_url_job_downloads_first_and_marks_done(tmp_path):
    r, core = runner(tmp_path)
    job = r.start(request(domain="web3", profile="laptop"))
    events = wait(job)
    assert job.state == "finished"
    steps = [e["step"] for e in events if e["type"] == "step_started"]
    assert steps[0] == "download" and steps[-1] == "outputs"
    assert core.calls.index("check_keys") < core.calls.index("download") < core.calls.index("pipeline")
    assert "glossary:web3" in core.calls
    assert core.settings.name == "yt-abc123" and core.settings.source.name == "A talk.mp4"
    assert "Title: A talk" in core.settings.context
    folder, files, cost = core.done_marked[0]
    assert folder.name == "2026-11-01 A talk" and "A talk.bilingual.ass" in files
    assert events[-1]["type"] == "finished"
    download = [(e["position"], e["total"], e["unit"]) for e in events
                if e["type"] == "step_progress" and e["step"] == "download"]
    assert download[0] == (250_000, 1_000_000, "byte") and download[-1] == (1_000_000, 1_000_000, "byte")
    # no burn-in in the laptop profile: no positions for the outputs step
    assert not [e for e in events if e["type"] == "step_progress" and e["step"] == "outputs"]


def test_positions_are_throttled_and_never_raise(tmp_path):
    from talk_autopsy.time_estimate import TimeEstimator
    now = [0.0]
    job = jobs.Job(id="j", request=request(), preview=None, steps=[], started_wall="", clock=lambda: now[0])
    positions = jobs.Positions(job, TimeEstimator(None, 600.0, ["download"]), "download", "byte")
    for done, at in ((10, 0.1), (20, 0.2), (30, 0.3), (40, 0.9)):
        now[0] = at
        positions(done, 100)
    positions(100, 100)  # the last one always goes out
    now[0] = 2.0
    positions(5, None)  # an unknown total: no bar, no estimate, no error
    sent = [(e["position"], e["total"]) for e in job.events_after(0)[0]]
    assert sent == [(10, 100), (40, 100), (100, 100), (5, None)]


def test_replay_from_an_id(tmp_path, media):
    r, _ = runner(tmp_path)
    job = r.start(request(mode="file", url=None, path=str(media)))
    events = wait(job)
    later, ended = job.events_after(5)
    assert ended and [e["id"] for e in later] == [e["id"] for e in events[5:]]
    assert job.events_after(len(events))[0] == []
    assert job.events_after(10_000)[0] == [] and job.events_after(-3)[0] == events


def test_one_job_at_a_time(tmp_path, media):
    r, core = runner(tmp_path)
    core.gate = threading.Event()
    first = r.start(request(mode="file", url=None, path=str(media)))
    with pytest.raises(BusyError) as e:
        r.start(request(mode="file", url=None, path=str(media)))
    assert e.value.active_id == first.id and r.current() is first
    core.gate.set()
    wait(first)
    second = r.start(request(mode="file", url=None, path=str(media)))
    wait(second)
    assert second.state == "finished" and r.current() is second


# --- the cost gate ------------------------------------------------------------------------


def test_over_the_limit_needs_the_exact_amount(tmp_path):
    r, core = runner(tmp_path, duration=3 * 3600)  # about 3.87 USD with speech recognition
    amount = r.preview(request()).amount_usd
    assert amount > 3.0
    with pytest.raises(GateError) as e:
        r.start(request())
    assert e.value.code == "confirm_cost" and e.value.amount_usd == amount
    with pytest.raises(GateError) as e:
        r.start(request(confirmed_cost_usd=amount - 0.5))
    assert e.value.code == "cost_mismatch"
    assert r.active_id is None and "pipeline" not in core.calls
    job = r.start(request(confirmed_cost_usd=amount))
    wait(job)
    assert job.state == "finished" and core.settings.max_cost_usd == amount


def test_no_estimate_needs_a_confirmation(tmp_path):
    r, core = runner(tmp_path, duration=None)
    with pytest.raises(GateError) as e:
        r.start(request())
    assert e.value.code == "confirm_no_estimate"
    job = r.start(request(confirmed_no_estimate=True))
    wait(job)
    assert job.state == "finished" and core.settings.max_cost_usd == 3.0


def test_under_the_limit_needs_no_confirmation(tmp_path):
    r, core = runner(tmp_path, duration=600)
    wait(r.start(request()))
    assert "pipeline" in core.calls


# --- failures --------------------------------------------------------------------------------


def test_a_core_failure_is_one_plain_line(tmp_path, media):
    error = pipeline.PipelineError("the Claude steps are estimated at 4.00 USD, above the limit of 3.00 USD; "
                                   "raise --max-cost to confirm")
    r, _ = runner(tmp_path, fail=error)
    job = r.start(request(mode="file", url=None, path=str(media)))
    events = wait(job)
    assert job.state == "failed" and events[-1]["type"] == "failed"
    assert events[-1]["hint"] == "cost_limit" and "above the limit" in events[-1]["reason"]
    assert r.active_id is None


def test_an_unexpected_failure_has_no_stack_trace(tmp_path, media):
    r, _ = runner(tmp_path, fail=KeyError("segments"))
    job = r.start(request(mode="file", url=None, path=str(media)))
    end = wait(job)[-1]
    assert end["type"] == "failed" and end["hint"] == "log"
    assert "Traceback" not in end["reason"] and "KeyError" in end["reason"]
    assert end["step"] == "srt"  # the last step that started... or ended: never None mid-pipeline


def test_missing_key_fails_before_any_work(tmp_path):
    r, core = runner(tmp_path, fail_keys=ConfigurationError("ANTHROPIC_API_KEY is missing or invalid\nmore"))
    end = wait(r.start(request()))[-1]
    assert end["type"] == "failed" and end["hint"] == "doctor"
    assert end["reason"] == "ANTHROPIC_API_KEY is missing or invalid"
    assert "download" not in core.calls


def test_an_exception_inside_the_callback_does_not_kill_the_job_silently(tmp_path, media, monkeypatch):
    real = jobs._Progress._handle

    def flaky(self, event):
        if event.event == "block-done":
            raise ValueError("display bug")
        real(self, event)
    monkeypatch.setattr(jobs._Progress, "_handle", flaky)
    r, _ = runner(tmp_path)
    job = r.start(request(mode="file", url=None, path=str(media)))
    events = wait(job)
    assert job.state == "finished"
    notices = [e for e in events if e["type"] == "notice" and e["code"] == "progress_display_error"]
    assert len(notices) == 1 and "ValueError" in notices[0]["text"]


def test_no_key_value_can_reach_an_event(tmp_path, media, monkeypatch):
    secret = os.environ["ANTHROPIC_API_KEY"]  # the conftest placeholder
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "fake-assemblyai-secret-value-123")
    script = [pe("stt", "step-start", f"AssemblyAI key {secret}"),
              pe("stt", "fallback", "upload failed with key fake-assemblyai-secret-value-123")]
    r, _ = runner(tmp_path, script=script,
                  fail=pipeline.PipelineError(f"request failed: Authorization {secret}"))
    events = wait(r.start(request(mode="file", url=None, path=str(media))))
    dump = json.dumps(events)
    assert secret not in dump and "fake-assemblyai-secret-value-123" not in dump
    assert dump.count(jobs.HIDDEN) == 3


# --- cached runs, caps, parsing ----------------------------------------------------------------


def test_a_cached_run_does_not_touch_the_history(tmp_path, media):
    r, _ = runner(tmp_path, script=CACHED_RUN)
    events = wait(r.start(request(mode="file", url=None, path=str(media), profile="laptop")))
    skipped = [e["step"] for e in events if e["type"] == "step_finished" and e["skipped"]]
    assert skipped == ["stt", "glossary", "stt2", "align", "fix", "translate"]
    assert all(e["cost_usd"] == 0 for e in events if e["type"] == "step_finished")
    history = tmp_path / "history.json"
    rates = json.loads(history.read_text(encoding="utf-8")) if history.exists() else {"machines": {}}
    for machine in rates["machines"].values():
        assert not set(machine["rates"]) & {"stt", "glossary", "stt2", "fix", "translate"}


def test_text_events_are_capped(tmp_path, media, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_EVENTS", 105)  # text stops 100 events before the cap
    monkeypatch.setattr(jobs, "ROWS_PER_EVENT", 1)
    r, _ = runner(tmp_path)
    events = wait(r.start(request(mode="file", url=None, path=str(media))))
    assert len([e for e in events if e.get("code") == "text_truncated"]) <= 1
    assert events[-1]["type"] == "finished"


def test_text_is_shortened(tmp_path, media, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_TEXT_CHARS", 10)
    r, _ = runner(tmp_path)
    events = wait(r.start(request(mode="file", url=None, path=str(media))))
    rows = next(e for e in events if e["type"] == "transcript")["rows"]
    assert rows[1]["text"] == "We use cl…"


def test_live_blocks_are_not_repeated_from_the_file(tmp_path, media):
    block = {"position": 1, "total": 1, "failed": False, "ids": [1, 2],
             "segments": [{"id": 1, "text": "Hello agents."}, {"id": 2, "text": "We use Claude Code."}],
             "edits": [{"id": 2, "before": "cloud code", "after": "Claude Code"}]}
    script = [pipeline.PipelineEvent("fix", "step-start", 0, 7, 0, 0, 0, "pipeline", "correction-v2"),
              pipeline.PipelineEvent("fix", "block-done", 0, 7, 0, 0, 0, "pipeline", "block 1/1", data=block),
              pipeline.PipelineEvent("fix", "step-done", 0, 7, 0, 0, 0, "pipeline", "fixed.json")]
    r, core = runner(tmp_path)
    core.script = script
    job = r.start(request(mode="file", url=None, path=str(media)))
    events = wait(job)
    corrected = [e for e in events if e["type"] == "corrected"]
    assert len(corrected) == 1 and "from_file" not in corrected[0]
    assert corrected[0]["segments"][1]["text"] == "We use Claude Code." and len(corrected[0]["edits"]) == 1


def test_long_text_is_shortened():
    assert jobs.short("word " * 200, 20).endswith("…") and len(jobs.short("word " * 200, 20)) == 20


def test_step_costs_from_the_pipeline_line():
    assert jobs.step_costs("raw.json, speech recognition 0.0400 USD") == (0.0, 0.04)
    assert jobs.step_costs("fixed.json, 0.0400 USD") == (0.04, 0.0)
    assert jobs.step_costs("glossary.md, 0.0090 USD; 3 terms cost 9.99 USD") == (0.009, 0.0)
    assert jobs.step_costs("aligned.json") == (0.0, 0.0)


def test_scrub_reaches_nested_values():
    out = jobs.scrub({"a": ["x secret-value-1 y", {"b": "secret-value-1"}], "n": 3}, ["secret-value-1"])
    assert out == {"a": ["x [hidden] y", {"b": "[hidden]"}], "n": 3}


def test_summary_for_a_page_reload(tmp_path, media):
    r, _ = runner(tmp_path)
    job = r.start(request(mode="file", url=None, path=str(media)))
    events = wait(job)
    summary = job.summary(3.0)
    assert summary["state"] == "finished" and summary["last_event_id"] == events[-1]["id"]
    assert summary["steps"][0] == "stt" and summary["steps"][-1] == "outputs"
    assert summary["request"]["source"] == str(media)
    json.dumps(summary)  # serializable


def test_full_run_script_matches_the_pipeline_steps():
    steps = {e.stage for e in FULL_RUN if e.event in ("step-done", "step-skipped")}
    assert steps == set(pipeline.STEPS)


def test_output_step_names_keep_slow_and_fast_outputs_apart():
    assert jobs.output_step(["srt", "mp4-burned"]) == "outputs_burn"
    assert jobs.output_step(["srt", "ass", "bilingual", "mp4-soft"]) == "outputs_soft"
    assert jobs.output_step(["srt", "ass", "bilingual"]) == "outputs_subs"
    assert jobs.estimator_steps("url", ["srt"])[0] == "download"


def test_steps_without_a_start_event_are_timed_unless_passed_through(tmp_path, media):
    script = [pe("stt", "step-start", "AssemblyAI"), pe("stt", "step-done", "raw.json"),
              pe("stt2", "step-done", "raw2.json; passed through (no glossary terms)"),
              pe("srt", "step-done", "talk.lt.srt")]
    ticks = iter(range(10_000))
    core = FakeCore(tmp_path, script=script)
    r = JobRunner(core=core, history_file=tmp_path / "history.json", tick_s=3600,
                  clock=lambda: float(next(ticks)))  # every reading is one second later
    wait(r.start(request(mode="file", url=None, path=str(media))))
    machines = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))["machines"]
    rates = next(iter(machines.values()))["rates"]
    assert "srt" in rates and rates["srt"][0] > 0  # timed from the end of the previous step
    assert "stt2" not in rates  # a pass-through says nothing about a real second pass


def test_numbers_of_pipeline_events_win_over_their_text(tmp_path, media):
    data = {"skipped": False, "seconds": 3.0, "cost_usd": 0.5, "stt_cost_usd": 0.0,
            "tokens": {"input": 7, "output": 8, "cache_write": 0, "cache_read": 0}}
    script = [pipeline.PipelineEvent("fix", "estimate", 0, 7, 0, 0, 0, "pipeline", "garbled",
                                     data={"usd": 0.25, "blocks": 1, "input_tokens": 1, "output_tokens": 1,
                                           "part": "fix"}),
              pipeline.PipelineEvent("fix", "step-start", 0, 7, 0, 0, 0, "pipeline", "correction-v2"),
              pipeline.PipelineEvent("fix", "step-done", 0, 7, 0, 0, 0, "pipeline", "fixed.json, 0.0400 USD",
                                     data=data)]
    r, core = runner(tmp_path)
    core.script = script
    job = r.start(request(mode="file", url=None, path=str(media)))
    events = wait(job)
    fix = next(e for e in events if e["type"] == "step_finished" and e["step"] == "fix")
    assert fix["cost_usd"] == 0.5 and fix["tokens"]["input"] == 7
    assert [e["claude_usd"] for e in events if "claude_usd" in e] == [0.25]
