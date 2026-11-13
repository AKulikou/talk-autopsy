"""
A run stopped by a failure on a service's side (owner, 2026-11-13: a 34-minute
video stopped on HTTP 503 from the Claude API and the page said "Unexpected
error (ServiceUnavailableError)"): the Failed event names the service, the
kind and the status, keeps the exception class for the technical details only,
and says what was spent, including the paid answers of the step that stopped.
Every run is logged in the result folder, so a run that finishes after failed
attempts reports the video's total with them. The console says the same.
"""

import json
import logging
import time
from types import SimpleNamespace

import anthropic
import httpx2

from talk_autopsy import blocks
from talk_autopsy.core import structured_call
from talk_autopsy.web import jobs, money
from talk_autopsy.web.jobs import JobRequest, JobRunner
from tests.web_fakes import FULL_RUN, FakeCore

USAGE = SimpleNamespace(input_tokens=10_000, output_tokens=4_000, cache_creation_input_tokens=0,
                        cache_read_input_tokens=0)
MODEL = "claude-sonnet-5-5"


def unavailable():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.ServiceUnavailableError("Error code: 503", body=None,
                                             response=httpx2.Response(503, request=request))


class StopsInCorrection(FakeCore):
    """Runs to the correction step, pays for one block (spend_observer), then the service is gone."""

    def __init__(self, root, failing: bool = True):
        super().__init__(root)
        self.failing = failing

    def run_pipeline(self, settings, progress):
        if not self.failing:
            return super().run_pipeline(settings, progress)
        self.calls.append("pipeline")
        self.settings = settings
        upto = next(i for i, e in enumerate(FULL_RUN) if e.stage == "fix" and e.event == "step-start")
        for event in FULL_RUN[:upto + 2]:  # fix started, its block 1 done
            progress(event)
        structured_call.spend_observer.get()(MODEL, USAGE)
        raise unavailable()


def start(tmp_path, core, media):
    r = JobRunner(core=core, history_file=tmp_path / "history.json", tick_s=3600)
    job = r.start(JobRequest(mode="file", path=str(media), target_lang="lt"))
    while job.state == "running":
        time.sleep(0.01)
    return job.events_after(0)[0][-1]


def test_the_failed_screen_names_the_service_and_what_was_spent(tmp_path, caplog):
    media = tmp_path / "talk.mp4"
    media.write_bytes(b"video")
    with caplog.at_level(logging.INFO, logger="talk_autopsy.console"):
        end = start(tmp_path, StopsInCorrection(tmp_path), media)
    assert end["type"] == "failed" and end["hint"] == "service"
    assert (end["service"], end["kind"], end["status"]) == ("claude", "unavailable", 503)
    assert end["step"] == "fix" and end["error_type"] == "ServiceUnavailableError"
    assert "ServiceUnavailableError" in end["reason"] and "4 tries" in end["reason"]  # technical details
    block = blocks.usage_cost(MODEL, USAGE)
    assert end["cost_usd"] == round(0.009 + block, 4)  # the glossary and the paid correction block
    assert end["stt_cost_usd"] == 0.08
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Failed at")][0]
    assert line.startswith("Failed at fix: Claude API unavailable (HTTP 503), on the service's side; "
                           "finished steps are kept. Spent: ")


def test_done_after_a_failed_attempt_gives_the_videos_total(tmp_path, caplog):
    media = tmp_path / "talk.mp4"
    media.write_bytes(b"video")
    failed = start(tmp_path, StopsInCorrection(tmp_path), media)
    with caplog.at_level(logging.INFO, logger="talk_autopsy.console"):
        done = start(tmp_path, StopsInCorrection(tmp_path, failing=False), media)
    assert done["type"] == "finished"
    assert done["attempts"] == {"failed": 1,
                                "claude_usd": round(done["cost_usd"] + failed["cost_usd"], 4),
                                "stt_usd": round(done["stt_cost_usd"] + failed["stt_cost_usd"], 4)}
    folder = tmp_path / "output" / "talk"
    log = [json.loads(line) for line in (folder / ".talk-autopsy" / money.RUNS_LOG).read_text().splitlines()]
    assert [run["state"] for run in log] == ["failed", "finished"]
    marker = json.loads((folder / ".talk-autopsy" / jobs.RESULT_MARKER).read_text(encoding="utf-8"))
    assert marker["attempts"] == done["attempts"]
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Finished")][0]
    assert "; with 1 failed attempt(s): " in line
    # the next run starts a new count: the earlier attempts belong to the earlier result
    again = start(tmp_path, StopsInCorrection(tmp_path, failing=False), media)
    assert again["attempts"] is None


def test_the_console_and_page_rule_for_money():
    assert money.money_text(0.4, 0.0, {"stt": True}) == (
        "Spent: 0.40 USD — Claude 0.40 + speech recognition 0.00 (from cache)")
    assert money.money_text(0.004, 0.0) == "Spent: < 0.01 USD — Claude < 0.01 + speech recognition 0.00"
    assert money.amount(0) == "0.00" and money.amount(12.345) == "12.35"


def test_an_unexpected_failure_keeps_its_class_out_of_the_main_text(tmp_path):
    media = tmp_path / "talk.mp4"
    media.write_bytes(b"video")
    end = start(tmp_path, FakeCore(tmp_path, fail=KeyError("segments")), media)
    assert end["hint"] == "log" and end["service"] is None
    assert end["error_type"] == "KeyError"  # for "Technical details"; the page's sentence is fail_log
