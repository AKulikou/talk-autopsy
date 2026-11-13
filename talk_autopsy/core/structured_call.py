"""
One structured-output request to Claude, shared by every Claude stage.

A schema-valid response can still be garbage: in a real Startup Autopsy run the
model broke off the analysis mid-sentence and filled the remaining fields with
fragments ("killer_risk": "none", empty lists). Each caller passes a validator;
output it rejects is retried once, then reported as an error instead of
reaching a report.

Prompt caching: with `cached_prefix`, the system prompt becomes two blocks, the
shared prefix first with a cache breakpoint, then the caller's own system text.
Calls that share the prefix (the blocks of one video with its glossary) then read
it from the cache at a fraction of the input price instead of paying for it each
time. Cached tokens are not counted in usage.input_tokens; they are reported
separately and priced by core/token_summary.py.

Observability (pipeline-v3.1): `result_observer`, when set in the calling
context, is told about every accepted result (label and parsed answer) just
before it is returned, so a display can show a block's text as soon as it is
ready. It never changes a request or a result; an observer that raises is
logged and ignored.

Rejected answers (pipeline-v3.4): when every attempt is rejected,
InvalidResultError carries the parsed answers in order (`rejected`), so a
caller can still use the parts its own checks pass (correction and translation
revert only the segments that failed; docs/decisions.md). The requests and the
retry are unchanged. `tell_observer` lets such a caller report the answer it
finally used.

Transient failures (pipeline-v3.5): a request that fails on the service's side
(unavailable, overloaded, rate limited, timeout, dropped connection) is sent
again, unchanged, for about a minute (`core/transient.py`) before the failure
stands. `spend_observer`, when set, is told the model and usage of every paid
response (accepted or rejected), so a display can show what a run spent before
it stopped; like `result_observer` it never changes a request or a result.
"""

import logging
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from functools import partial

from anthropic import Anthropic
from pydantic import BaseModel

from talk_autopsy.core.transient import retrying

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
# A server-side tool loop (web search) can pause a long turn; resume it this many times.
MAX_CONTINUATIONS = 3
EPHEMERAL = {"type": "ephemeral"}
# Server-side refusal fallback, "default" form: the API re-runs a declined request on the
# model Anthropic recommends for the refusal category (Claude API only; not for Haiku).
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# pipeline-v3.1, observability only: (label, accepted result) -> None, set by the pipeline around a step.
result_observer: ContextVar[Callable[[str, BaseModel], None] | None] = ContextVar("result_observer",
                                                                                  default=None)
# pipeline-v3.5, observability only: (model asked, usage of one paid response) -> None.
spend_observer: ContextVar[Callable[[str, object], None] | None] = ContextVar("spend_observer", default=None)


def _tell_spend(model: str, usage) -> None:
    observer = spend_observer.get()
    if observer is None:
        return
    try:
        observer(model, usage)
    except Exception:
        logger.exception("the spend observer failed; the call goes on")


def tell_observer(label: str, result: BaseModel, reverted: list[int] | None = None) -> None:
    """
    Pass an accepted result to the observer, if any; its failure never reaches the call.
    `reverted`: ids of segments the caller put back to their input (pipeline-v3.4).
    """
    observer = result_observer.get()
    if observer is None:
        return
    try:
        if reverted:
            observer(label, result, reverted=reverted)
        else:
            observer(label, result)
    except Exception:
        logger.exception(f"{label}: the result observer failed; the call goes on")


class InvalidResultError(RuntimeError):
    """Raised when the model keeps returning structurally valid but unusable output."""

    def __init__(self, message: str, usage: "Usage", rejected: list | None = None):
        super().__init__(message)
        # The rejected attempts are billed, so callers still report their tokens.
        self.usage = usage
        # The parsed answer of every rejected attempt, in order (pipeline-v3.4).
        self.rejected = list(rejected or [])


@dataclass
class Usage:
    """Billed tokens of one logical call, summed over attempts and continuations."""

    input_tokens: int = 0  # uncached input only
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    web_searches: int = 0  # billed per search, on top of tokens

    def add(self, usage) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_creation_input_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.cache_read_input_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        server_tools = getattr(usage, "server_tool_use", None)
        self.web_searches += getattr(server_tools, "web_search_requests", 0) or 0

    def as_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "web_searches": self.web_searches,
        }


def length_problems(field: str, text: str, min_chars: int, max_chars: int) -> list[str]:
    """A too short or too long field is a sign of truncated or bled-over output."""
    length = len(text.strip())
    if length < min_chars:
        return [f"{field} too short ({text.strip()!r})"]
    if length > max_chars:
        return [f"{field} too long ({length} chars, starts {text.strip()[:60]!r})"]
    return []


def billed_usage(error: Exception) -> dict:
    """Token fields for an error result: rejected attempts are billed, other failures are not known."""
    if isinstance(error, InvalidResultError):
        return error.usage.as_dict()
    return {}


def build_system(system: str, cached_prefix: str | None) -> str | list[dict]:
    """
    The system parameter: plain text, or with a shared prefix two text blocks
    where only the prefix carries the cache breakpoint. Everything that differs
    between calls must come after the breakpoint, or the calls share no cache.
    """
    if cached_prefix is None:
        return system
    blocks: list[dict] = [{"type": "text", "text": cached_prefix, "cache_control": EPHEMERAL}]
    if system:
        blocks.append({"type": "text", "text": system})
    return blocks


@dataclass
class StructuredResponse[ResultT: BaseModel]:
    result: ResultT
    usage: Usage
    model: str = ""  # the model that produced the accepted answer (differs after a refusal fallback)


def request_structured[ResultT: BaseModel](
    client: Anthropic,
    *,
    label: str,
    model: str,
    max_tokens: int,
    system: str,
    user: str,
    output_format: type[ResultT],
    validate: Callable[[ResultT], list[str]],
    cached_prefix: str | None = None,
    thinking: bool = False,
    tools: list[dict] | None = None,
    effort: str | None = None,
    refusal_fallback: bool = False,
) -> StructuredResponse[ResultT]:
    """
    Send the request, check why generation stopped, and validate the parsed result.

    Args:
        cached_prefix: Text shared by several calls (rules and shared context),
            sent before `system` with a cache breakpoint. None = no caching.
        thinking: adaptive thinking before answering.
        tools: server-side tools (web search); a turn they pause is resumed up to
            MAX_CONTINUATIONS times. Tools come before the system prompt in the
            cache order, so calls with different tools never share a cache.
        effort: output_config.effort (None = the model's default).
        refusal_fallback: send `fallbacks: "default"` (beta FALLBACK_BETA) through
            the beta endpoint, so a request the model's classifiers decline is
            answered by the fallback model instead; the serving model is in
            the result's `model`.

    Raises:
        RuntimeError: on truncation or refusal
        InvalidResultError: if every attempt returned output rejected by `validate`
        anthropic.APIError: on API failures (transient ones only after about a minute of retries)
    """
    usage = Usage()
    extra: dict = {"thinking": {"type": "adaptive"}} if thinking else {}
    if tools:
        extra["tools"] = tools
    if effort:
        extra["output_config"] = {"effort": effort}
    if refusal_fallback:
        extra["betas"] = [FALLBACK_BETA]
        extra["fallbacks"] = "default"
    parse = client.beta.messages.parse if refusal_fallback else client.messages.parse
    system_param = build_system(system, cached_prefix)
    problems: list[str] = []
    rejected: list[ResultT] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        logger.debug(
            f"{label}: request model={model} max_tokens={max_tokens} thinking={thinking} effort={effort} "
            f"tools={[t['name'] for t in tools or []]} cached_prefix_chars={len(cached_prefix or '')} "
            f"system_chars={len(system)} user_chars={len(user)}"
        )
        messages = [{"role": "user", "content": user}]
        paused_content = []
        for continuation in range(MAX_CONTINUATIONS + 1):
            response = retrying(partial(
                parse,
                model=model,
                max_tokens=max_tokens,
                system=system_param,
                messages=messages,
                output_format=output_format,
                **extra,
            ), label)
            usage.add(response.usage)
            _tell_spend(model, response.usage)
            logger.debug(
                f"{label}: response request_id={response._request_id} "
                f"stop_reason={response.stop_reason} input_tokens={response.usage.input_tokens} "
                f"cache_write={getattr(response.usage, 'cache_creation_input_tokens', 0) or 0} "
                f"cache_read={getattr(response.usage, 'cache_read_input_tokens', 0) or 0} "
                f"output_tokens={response.usage.output_tokens}"
            )
            if response.stop_reason != "pause_turn":
                break
            if continuation == MAX_CONTINUATIONS:
                raise RuntimeError(
                    f"Server tool turn still paused after {MAX_CONTINUATIONS} continuations"
                )
            # The API resumes from the trailing server_tool_use block; no extra user message.
            paused_content += response.content
            messages = [{"role": "user", "content": user}, {"role": "assistant", "content": paused_content}]

        if response.stop_reason == "max_tokens":
            raise RuntimeError(
                f"Response truncated at max_tokens={max_tokens}; increase MAX_TOKENS in config.py"
            )
        if response.stop_reason == "refusal":
            raise RuntimeError("Model refused the request")

        problems = validate(response.parsed_output)
        if not problems:
            if cached_prefix is not None and not (
                usage.cache_creation_input_tokens or usage.cache_read_input_tokens
            ):
                logger.warning(
                    f"{label}: caching was requested but nothing was cached or read; the prefix is "
                    "probably shorter than the model's minimum cacheable length"
                )
            served_by = str(getattr(response, "model", "") or model)
            tell_observer(label, response.parsed_output)
            return StructuredResponse(response.parsed_output, usage, served_by)
        rejected.append(response.parsed_output)
        logger.warning(
            f"{label}: invalid result on attempt {attempt}/{MAX_ATTEMPTS} "
            f"(request_id={response._request_id}): {'; '.join(problems)}"
        )

    raise InvalidResultError(
        f"{MAX_ATTEMPTS} attempts returned malformed output: {'; '.join(problems)}",
        usage,
        rejected,
    )
