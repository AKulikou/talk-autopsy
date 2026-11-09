"""
Anthropic client shared by every Claude stage.

Every API response carries anthropic-ratelimit-* headers (requests and tokens
remaining in the current window, and when it resets). messages.parse() does not
expose response headers, so an HTTP response hook logs them at DEBUG; the log file
then shows how close a run came to the rate limits.
"""

import logging
from functools import lru_cache

from anthropic import Anthropic, DefaultHttpxClient

from talk_autopsy.config import require_api_key
from talk_autopsy.core.batch_client import BatchingClient
from talk_autopsy.core.token_summary import use_batch_pricing

logger = logging.getLogger(__name__)

RATE_LIMIT_HEADER_PREFIX = "anthropic-ratelimit-"


def _log_rate_limits(response) -> None:
    limits = {
        name[len(RATE_LIMIT_HEADER_PREFIX):]: value
        for name, value in response.headers.items()
        if name.lower().startswith(RATE_LIMIT_HEADER_PREFIX)
    }
    if limits:
        logger.debug(
            f"rate limits after HTTP {response.status_code}: "
            + ", ".join(f"{name}={value}" for name, value in sorted(limits.items()))
        )


# Set by use_batches(): a benchmark run can send every request through the Batches API.
_batching_client: BatchingClient | None = None


def use_batches(window_s: float = 10.0, poll_s: float = 30.0) -> BatchingClient:
    """
    From now on get_client() returns a client that answers messages.parse()
    through Message Batches (half price, minutes of latency). For benchmark runs only.
    """
    global _batching_client
    _batching_client = BatchingClient(_direct_client(), window_s=window_s, poll_s=poll_s)
    use_batch_pricing()
    return _batching_client


def get_client() -> Anthropic | BatchingClient:
    """The shared client: direct, or batching after use_batches()."""
    return _batching_client or _direct_client()


@lru_cache(maxsize=1)
def _direct_client() -> Anthropic:
    """One client per process: it reuses connections and is safe to share across threads."""
    return Anthropic(
        api_key=require_api_key(),
        http_client=DefaultHttpxClient(event_hooks={"response": [_log_rate_limits]}),
    )
