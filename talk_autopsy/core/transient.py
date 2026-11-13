"""
Failures on a service's side that pass by themselves (pipeline-v3.5).

The bug (2026-11-13): a 34-minute video stopped in the correction step on
"HTTP 503 Service Unavailable" from the Claude API. The Anthropic SDK retried
twice, after 0.4 and 0.9 s, and gave up within five seconds; a manual restart
two minutes later went through. A short outage cost the whole run.

`retrying` calls a request again after such a failure: three more tries,
after 5, 15 and 30 s (about a minute in all, on top of the SDK's own two
quick retries inside each try, which stay). A failure on our side (a bad
request, a missing or wrong key, a refusal) is never retried. A failed try
returned no answer, so nothing is counted or written for it; the caller adds
the usage of the answer that finally arrives, once.

`classify` names a failure for people: which service, what kind (unavailable,
overloaded, rate limited, timeout, connection), the HTTP status. The web page
uses it to say in plain words that the service was unavailable.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DELAYS_S = (5.0, 15.0, 30.0)  # waits before the second, third and fourth try
TRANSIENT_STATUS = {408, 409, 429}  # and every 5xx: the statuses the Anthropic SDK itself retries
SERVICES = {"anthropic": "claude", "assemblyai": "assemblyai", "google": "gemini"}
sleep = time.sleep  # tests replace it


@dataclass(frozen=True)
class Transient:
    service: str  # "claude", "assemblyai", "gemini", or "network" when no service is known
    kind: str  # "unavailable", "overloaded", "rate_limited", "timeout", "connection"
    status: int | None = None

    def text(self) -> str:
        status = f"HTTP {self.status}" if self.status else self.kind
        return f"{self.service} {self.kind} ({status})" if self.status else f"{self.service} {self.kind}"


def _service(exc: BaseException) -> str:
    root = type(exc).__module__.split(".")[0]
    return SERVICES.get(root, "network")


def _status(exc: BaseException) -> int | None:
    for name in ("status_code", "code", "status"):
        value = getattr(exc, name, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    return None


def _one(exc: BaseException) -> Transient | None:
    names = {cls.__name__ for cls in type(exc).__mro__}
    timeout = isinstance(exc, TimeoutError) or any("Timeout" in n for n in names)
    connection = isinstance(exc, ConnectionError) or bool(
        names & {"APIConnectionError", "ConnectError", "NetworkError", "RemoteProtocolError", "ReadError",
                 "WriteError", "ConnectionResetError"})
    status = _status(exc)
    if status is not None and (status in TRANSIENT_STATUS or status >= 500):
        kind = {429: "rate_limited", 529: "overloaded", 408: "timeout", 504: "timeout"}.get(status,
                                                                                           "unavailable")
        return Transient(_service(exc), kind, status)
    if status is None and (timeout or connection):
        return Transient(_service(exc), "timeout" if timeout else "connection")
    return None


def classify(exc: BaseException) -> Transient | None:
    """The failure as a transient one, or None (also for an error raised from a transient one's cause)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        found = _one(current)
        if found is not None:
            return found
        current = current.__cause__
    return None


def retrying[T](call: Callable[[], T], label: str) -> T:
    """`call()`, tried again after a transient failure (DELAYS_S); any other failure is raised at once."""
    tries = len(DELAYS_S) + 1
    for attempt in range(1, tries + 1):
        try:
            return call()
        except Exception as exc:
            found = classify(exc)
            if found is None or attempt == tries:
                if found is not None:
                    logger.warning("%s: %s on the service's side; %d tries over about %.0f s failed",
                                   label, found.text(), tries, sum(DELAYS_S))
                raise
            wait = DELAYS_S[attempt - 1]
            logger.warning("%s: %s on the service's side; try %d of %d failed, trying again in %.0f s",
                           label, found.text(), attempt, tries, wait)
            sleep(wait)
    raise AssertionError("unreachable")
