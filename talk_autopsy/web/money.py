"""
How money is written (owner, 2026-11-13): total first, then each service;
two decimals; an amount above zero but below one cent is "< 0.01"; "(from
cache)" after a part that cost nothing because its steps were reused. The
page has the same rule in `static/money.js` (decimal comma in Russian and
Lithuanian); this is the English one, for the console.

    Spent: 0.40 USD — Claude 0.40 + speech recognition 0.00 (from cache)

Also the run log of a result folder (`.talk-autopsy/runs.jsonl`): one line
per finished or failed run, so the Done screen can give the total for a video
that needed more than one attempt.
"""

import datetime as dt
import json
import logging
from pathlib import Path

from talk_autopsy import youtube

logger = logging.getLogger(__name__)

RUNS_LOG = "runs.jsonl"
CLAUDE_STEPS = ("glossary", "fix", "translate")
STT_STEPS = ("stt", "stt2")


def amount(value: float | None) -> str:
    value = float(value or 0.0)
    return "< 0.01" if 0 < value < 0.01 else f"{value:.2f}"


def money_text(claude: float, stt: float, cached: dict | None = None, prefix: str = "Spent: ") -> str:
    cached = cached or {}

    def part(name: str, value: float, key: str) -> str:
        reused = " (from cache)" if cached.get(key) and not value else ""
        return f"{name} {amount(value)}{reused}"
    return (f"{prefix}{amount((claude or 0.0) + (stt or 0.0))} USD — "
            f"{part('Claude', claude, 'claude')} + {part('speech recognition', stt, 'stt')}")


def record_run(folder: Path, state: str, claude_usd: float, stt_usd: float) -> dict | None:
    """
    Adds this run to the folder's run log. For a finished run after failed
    attempts (since the last finished run), returns the total of this video
    with them: {"failed": n, "claude_usd": ..., "stt_usd": ...}; else None.
    A log that cannot be read or written is logged and left out.
    """
    try:
        log = youtube.service_dir(folder, create=True) / RUNS_LOG
        earlier = []
        if log.is_file():
            for line in log.read_text(encoding="utf-8").splitlines():
                try:
                    earlier.append(json.loads(line))
                except ValueError:
                    continue
        failed = []
        for run in earlier:
            failed = [] if run.get("state") == "finished" else failed + [run]
        with log.open("a", encoding="utf-8", newline="\n") as out:
            out.write(json.dumps({"ended": dt.datetime.now().isoformat(timespec="seconds"), "state": state,
                                  "claude_usd": round(claude_usd, 4), "stt_usd": round(stt_usd, 4)}) + "\n")
    except OSError:
        logger.exception("run log of %s not written", folder)
        return None
    if state != "finished" or not failed:
        return None
    return {"failed": len(failed),
            "claude_usd": round(claude_usd + sum(float(r.get("claude_usd") or 0) for r in failed), 4),
            "stt_usd": round(stt_usd + sum(float(r.get("stt_usd") or 0) for r in failed), 4)}
