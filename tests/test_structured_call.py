import logging

import pytest
from conftest import FakeClaude, response
from pydantic import BaseModel

from talk_autopsy.core.structured_call import (
    InvalidResultError,
    billed_usage,
    build_system,
    length_problems,
    request_structured,
)


class Answer(BaseModel):
    summary: str


def validate(answer: Answer) -> list[str]:
    return length_problems("summary", answer.summary, 10, 200)


GOOD = Answer(summary="A complete, sensible answer.")
GARBAGE = Answer(summary="none")


def call(client, **overrides):
    kwargs = {
        "label": "Test", "model": "claude-sonnet-4-6", "max_tokens": 16000,
        "system": "system", "user": "user", "output_format": Answer, "validate": validate,
    }
    kwargs.update(overrides)
    return request_structured(client, **kwargs)


def scripted(*answers, simulate_cache=False) -> FakeClaude:
    queue = list(answers)
    return FakeClaude(lambda kwargs: queue.pop(0), simulate_cache)


def test_valid_answer_is_returned_with_its_tokens():
    result = call(scripted(response(GOOD, input_tokens=100, output_tokens=50)))
    assert result.result.summary == GOOD.summary
    assert (result.usage.input_tokens, result.usage.output_tokens) == (100, 50)


def test_malformed_answer_is_retried_and_both_attempts_are_billed():
    client = scripted(
        response(GARBAGE, input_tokens=100, output_tokens=50),
        response(GOOD, input_tokens=100, output_tokens=50),
    )
    result = call(client)
    assert len(client.requests) == 2
    assert (result.usage.input_tokens, result.usage.output_tokens) == (200, 100)


def test_two_malformed_answers_raise_with_the_billed_tokens():
    with pytest.raises(InvalidResultError) as error:
        call(scripted(
            response(GARBAGE, input_tokens=100, output_tokens=50),
            response(GARBAGE, input_tokens=100, output_tokens=50),
        ))
    assert "summary too short" in str(error.value)
    assert billed_usage(error.value)["input_tokens"] == 200


@pytest.mark.parametrize("stop_reason, message", [("max_tokens", "truncated"), ("refusal", "refused")])
def test_truncated_or_refused_answers_are_errors_not_results(stop_reason, message):
    with pytest.raises(RuntimeError, match=message):
        call(scripted(response(GOOD, stop_reason=stop_reason)))


def test_paused_server_tool_turn_is_resumed_and_searches_counted():
    client = scripted(
        response(None, stop_reason="pause_turn", web_searches=2),
        response(GOOD, web_searches=1),
    )
    result = call(client)
    assert len(client.requests) == 2
    assert client.requests[1]["messages"][1]["role"] == "assistant"
    assert result.usage.web_searches == 3


def test_thinking_is_off_unless_requested():
    client = scripted(response(GOOD), response(GOOD))
    call(client)
    call(client, thinking=True)
    assert "thinking" not in client.requests[0]
    assert client.requests[1]["thinking"] == {"type": "adaptive"}


def test_without_prefix_the_system_prompt_is_plain_text():
    assert build_system("system", None) == "system"


def test_prefix_comes_first_and_only_it_carries_the_cache_breakpoint():
    blocks = build_system("own role", "shared rules and data")
    cached = {"type": "text", "text": "shared rules and data", "cache_control": {"type": "ephemeral"}}
    assert blocks[0] == cached
    assert blocks[1] == {"type": "text", "text": "own role"}


def test_cache_tokens_are_reported_separately_from_input():
    result = call(scripted(response(GOOD, input_tokens=40, cache_write=900)), cached_prefix="x" * 3600)
    assert result.usage.input_tokens == 40
    assert result.usage.cache_creation_input_tokens == 900
    assert result.usage.cache_read_input_tokens == 0


def test_a_requested_cache_that_never_happens_is_warned_about(caplog):
    with caplog.at_level(logging.WARNING):
        call(scripted(response(GOOD)), cached_prefix="too short to cache")
    assert "nothing was cached" in caplog.text


def test_no_warning_when_caching_was_not_requested(caplog):
    with caplog.at_level(logging.WARNING):
        call(scripted(response(GOOD)))
    assert caplog.text == ""
