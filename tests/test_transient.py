"""
Transient service failures (bug, 2026-11-13): a 34-minute video stopped in the
correction step on "HTTP 503 Service Unavailable" from the Claude API. The
SDK's own two quick retries (after 0.4 and 0.9 s) were spent within five
seconds; a manual restart two minutes later went through. A failure on the
service's side is now retried for about a minute before the run stops, without
counting any cost twice and without writing a partial result.
"""

import json

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from talk_autopsy import pipeline
from talk_autopsy.core import structured_call, transient
from talk_autopsy.correct import CorrectionAnswer
from tests.conftest import response
from tests.test_pipeline import claude_handler, default_path  # noqa: F401  (fixture)

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(cls, code: int):
    return cls(f"Error code: {code}", response=httpx2.Response(code, request=REQUEST), body=None)


TRANSIENT = [
    status_error(anthropic.ServiceUnavailableError, 503),
    status_error(anthropic.OverloadedError, 529),
    status_error(anthropic.RateLimitError, 429),
    status_error(anthropic.InternalServerError, 500),
    status_error(anthropic.GatewayTimeoutError, 504),
    anthropic.APIConnectionError(request=REQUEST),
    anthropic.APITimeoutError(request=REQUEST),
]


class Answer(BaseModel):
    text: str


@pytest.fixture
def waits(monkeypatch):
    """The waits between tries, recorded instead of slept."""
    recorded: list[float] = []
    monkeypatch.setattr(transient, "sleep", recorded.append)
    return recorded


def flaky(fake_claude, failures: list[BaseException], answer=None):
    """A client that raises `failures` in turn, then answers."""
    left = list(failures)

    def handler(kwargs):
        if left:
            raise left.pop(0)
        return answer(kwargs) if answer else response(Answer(text="ok"))
    return fake_claude(handler)


def ask(fake):
    return structured_call.request_structured(
        fake, label="test", model="claude-sonnet-5-5", max_tokens=100, system="", user="hello",
        output_format=Answer, validate=lambda a: [])


@pytest.mark.parametrize("error", TRANSIENT, ids=lambda e: type(e).__name__)
def test_a_failure_on_the_service_side_is_retried(fake_claude, waits, error):
    fake = flaky(fake_claude, [error, error])
    result = ask(fake)
    assert result.result.text == "ok" and len(fake.requests) == 3
    assert waits == list(transient.DELAYS_S[:2])
    assert result.usage.input_tokens == 1000 and result.usage.output_tokens == 500  # counted once


def test_the_retries_last_about_a_minute_then_the_failure_stands(fake_claude, waits):
    error = TRANSIENT[0]
    fake = flaky(fake_claude, [error] * 10)
    with pytest.raises(anthropic.ServiceUnavailableError):
        ask(fake)
    assert len(fake.requests) == len(transient.DELAYS_S) + 1
    assert waits == list(transient.DELAYS_S) and 45 <= sum(waits) <= 75


@pytest.mark.parametrize("error", [status_error(anthropic.BadRequestError, 400),
                                   status_error(anthropic.AuthenticationError, 401),
                                   status_error(anthropic.PermissionDeniedError, 403)],
                         ids=lambda e: type(e).__name__)
def test_a_failure_on_our_side_is_not_retried(fake_claude, waits, error):
    fake = flaky(fake_claude, [error])
    with pytest.raises(type(error)):
        ask(fake)
    assert len(fake.requests) == 1 and waits == []


def test_every_paid_response_is_reported_to_the_spend_observer(fake_claude, waits):
    spent = []
    token = structured_call.spend_observer.set(lambda model, usage: spent.append((model, usage.input_tokens)))
    try:
        ask(flaky(fake_claude, [TRANSIENT[0]]))
    finally:
        structured_call.spend_observer.reset(token)
    assert spent == [("claude-sonnet-5-5", 1000)]  # the failed try returned nothing to pay for


def _clean_then_rerun_fix_and_translate(settings, fake_claude, handler):
    """A clean run, then fix and translate again with `handler`: (clean, rerun) of (fixed.json, cost)."""
    fake_claude(claude_handler)
    clean = pipeline.run(settings)
    work = settings.work_dir
    clean_fixed = json.loads((work / "fixed.json").read_text(encoding="utf-8"))
    for name in ("fixed.json", "ru.json"):
        (work / name).unlink()
        (work / f"{name}.stamp.json").unlink()
    fake = fake_claude(handler)
    return clean, clean_fixed, fake


def test_a_retried_block_gives_the_same_result_and_cost(default_path, fake_claude, waits):  # noqa: F811
    settings, _ = default_path
    failed = []

    def handler(kwargs):
        if kwargs["output_format"] is CorrectionAnswer and not failed:
            failed.append(1)
            raise TRANSIENT[0]
        return claude_handler(kwargs)

    clean, clean_fixed, fake = _clean_then_rerun_fix_and_translate(settings, fake_claude, handler)
    rerun = pipeline.run(settings)
    fixed = json.loads((settings.work_dir / "fixed.json").read_text(encoding="utf-8"))
    assert failed and waits == [transient.DELAYS_S[0]]
    assert [s["text"] for s in fixed["segments"]] == [s["text"] for s in clean_fixed["segments"]]
    assert fixed["meta"]["correction"]["cost_usd"] == clean_fixed["meta"]["correction"]["cost_usd"]
    paid = [s.cost_usd for s in clean.steps if s.step in ("fix", "translate")]
    assert rerun.cost_usd == pytest.approx(sum(paid))


def test_a_run_stopped_by_the_service_writes_no_partial_result(default_path, fake_claude,  # noqa: F811
                                                               waits):
    settings, _ = default_path

    def handler(kwargs):
        if kwargs["output_format"] is CorrectionAnswer:
            raise TRANSIENT[0]
        return claude_handler(kwargs)

    _clean_then_rerun_fix_and_translate(settings, fake_claude, handler)
    with pytest.raises(anthropic.ServiceUnavailableError):
        pipeline.run(settings)
    work = settings.work_dir
    assert not (work / "fixed.json").exists() and not (work / "fixed.json.stamp.json").exists()
    assert (work / "glossary.md.stamp.json").is_file()  # the finished steps are kept


@pytest.mark.parametrize(("error", "kind"), [
    (TRANSIENT[0], "unavailable"), (TRANSIENT[1], "overloaded"), (TRANSIENT[2], "rate_limited"),
    (TRANSIENT[4], "timeout"), (TRANSIENT[5], "connection"), (TRANSIENT[6], "timeout")])
def test_a_transient_failure_is_named_for_the_page(error, kind):
    found = transient.classify(error)
    assert (found.service, found.kind) == ("claude", kind)
    assert transient.classify(status_error(anthropic.BadRequestError, 400)) is None
    assert transient.classify(ValueError("no")) is None
