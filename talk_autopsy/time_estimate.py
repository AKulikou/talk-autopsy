"""
How long a run will take on this machine, without ever blocking the run (F13).

Sources, in order:

1. **History** of this machine: seconds of work per second of video for each
   step, from earlier successful runs, stored in the user cache directory
   (`time-history.json`) and keyed by engine + model + compute type + CPU/GPU +
   core count (`MachineKey`). A key from another machine is never used.
2. **Built-in rough values** (`BUILTIN_RATES`) when this machine has no history
   for a step: the slower of the two operational runs of 2026-11-09
   (docs/experiments.md, Operational runs: T2.source 1673 s and real-01 2310 s,
   about 10 min per hour of video together) and the measured burn-in speed
   (`outputs.BURN_SPEED`). Such an estimate is flagged `first_run_rough`.
   Download and the subtitle-track MP4 have no measured value: they are listed
   in `excludes` until this machine has history for them.
3. **Measured speed**: once the run has done a minute of real (not cached)
   work, the remaining time is rescaled by how fast the finished work went
   against its prediction (`source = "measured"`).
4. Otherwise **no estimate** (`NO_ESTIMATE`).

Every public method catches its own errors: a missing or corrupted history
file, an unknown key, a zero or negative duration, a clock that goes backwards
or any other exception gives "no estimate" (or a skipped history write), never
an error to the caller. History is written after a successful run only,
atomically (temporary file + replace); cached steps are not recorded, so a
re-run from cache cannot drag the rates towards zero.
"""

import json
import logging
import math
import os
import statistics
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from talk_autopsy import outputs

logger = logging.getLogger(__name__)

HISTORY_FILE = "time-history.json"
HISTORY_VERSION = 1
KEEP_RATES = 5  # the last N measurements per step; the estimate uses their median
MEASURE_AFTER_S = 60.0  # seconds of real work before the measured speed replaces the prediction
SCALE_LIMITS = (0.2, 5.0)  # a measured speed further off than this is treated as noise

# Seconds of work per second of video. Pipeline steps: the slower of the two operational runs
# (2026-11-09; stt includes audio extraction and FLAC compression). Burn-in: outputs.BURN_SPEED.
BUILTIN_RATES: dict[str, float] = {
    "stt": 0.0256,       # 34.5 s / 1673 s and 59.1 s / 2310 s
    "glossary": 0.0104,  # 17.4 s / 1673 s
    "stt2": 0.0213,      # 35.6 s / 1673 s
    "align": 0.0,        # pass-through with measured times
    "fix": 0.0473,       # 79.2 s / 1673 s
    "translate": 0.0630,  # 105.4 s / 1673 s and 144.0 s / 2310 s
    "srt": 0.0007,       # 1.7 s / 2310 s
    "outputs_burn": outputs.BURN_SPEED,
}


@dataclass(frozen=True)
class TimeEstimate:
    """Seconds of work left, or `seconds is None` for "no estimate"."""

    seconds: float | None
    source: str  # "history" | "first_run_rough" | "measured" | "none"
    excludes: tuple[str, ...] = ()  # steps with no value, not counted (for example "download")

    @property
    def available(self) -> bool:
        return self.seconds is not None

    @property
    def first_run_rough(self) -> bool:
        return self.source == "first_run_rough"

    def as_dict(self) -> dict:
        return {"seconds": None if self.seconds is None else round(self.seconds),
                "source": self.source, "first_run_rough": self.first_run_rough,
                "excludes": list(self.excludes)}


NO_ESTIMATE = TimeEstimate(None, "none")


@dataclass(frozen=True)
class MachineKey:
    engine: str
    model: str
    compute_type: str
    device: str  # "cpu" or "gpu"
    cores: int

    def text(self) -> str:
        return "|".join((self.engine, self.model, self.compute_type, self.device, str(self.cores)))


def user_cache_dir(platform: str = sys.platform, env: Mapping[str, str] | None = None,
                   home: Path | None = None) -> Path:
    """
    talk-autopsy's folder in the user cache directory: %LOCALAPPDATA%\\talk-autopsy on
    Windows, ~/Library/Caches/talk-autopsy on macOS, $XDG_CACHE_HOME/talk-autopsy
    (default ~/.cache/talk-autopsy) elsewhere.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    if platform.startswith("win"):
        base = env.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
    elif platform == "darwin":
        base = str(home / "Library" / "Caches")
    else:
        base = env.get("XDG_CACHE_HOME") or str(home / ".cache")
    return Path(base) / "talk-autopsy"


def history_path() -> Path:
    return user_cache_dir() / HISTORY_FILE


def _valid_rate(value) -> bool:
    return (isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
            and value >= 0)


def load_history(path: Path, key: MachineKey) -> dict[str, float]:
    """{step: median rate} of this machine key; {} if the file is missing, unreadable or has no such key."""
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = data.get("machines", {}).get(key.text(), {}) if isinstance(data, dict) else {}
        rates = entry.get("rates", {}) if isinstance(entry, dict) else {}
        result = {}
        for step, values in rates.items() if isinstance(rates, dict) else ():
            if isinstance(values, list):
                good = [float(v) for v in values if _valid_rate(v)]
                if good:
                    result[str(step)] = statistics.median(good)
        return result
    except Exception as e:  # corrupted JSON, wrong types, permissions: no history
        logger.warning("time estimate: history %s not used (%s)", path, type(e).__name__)
        return {}


def write_history(path: Path, key: MachineKey, measured: Mapping[str, float]) -> bool:
    """
    Add `measured` ({step: rate}) to the history of `key`, keeping the last
    KEEP_RATES per step; written atomically. False (logged) on any failure.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        data: dict = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}  # a corrupted file is replaced rather than kept
        if not isinstance(data, dict) or not isinstance(data.get("machines"), dict):
            data = {"version": HISTORY_VERSION, "machines": {}}
        entry = data["machines"].setdefault(key.text(), {})
        if not isinstance(entry, dict) or not isinstance(entry.get("rates"), dict):
            entry = data["machines"][key.text()] = {"rates": {}}
        for step, rate in measured.items():
            old = entry["rates"].get(step)
            values = [v for v in old if _valid_rate(v)] if isinstance(old, list) else []
            entry["rates"][step] = (values + [round(float(rate), 9)])[-KEEP_RATES:]
        entry["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.warning("time estimate: history not written to %s (%s: %s)", path, type(e).__name__, e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


@dataclass
class _Step:
    started: float | None = None
    seconds: float | None = None  # set when finished
    skipped: bool = False
    fraction: float | None = None  # real position, when the step reports one
    measured: bool = False  # a real duration (given, or timed from step_started)


@dataclass
class TimeEstimator:
    """
    The time estimate of one run. Tell it what happens (`step_started`,
    `step_progress`, `step_finished`) and ask `remaining()` at any time; call
    `record()` once after a successful run. `steps` are the run's steps in order
    (pipeline steps plus "download", "outputs_burn" or "outputs").
    """

    key: MachineKey | None
    duration_s: float | None
    steps: list[str]
    history_file: Path | None = None
    clock: Callable[[], float] = time.monotonic
    _rates: dict[str, float] = field(default_factory=dict)
    _from_history: set[str] = field(default_factory=set)
    _state: dict[str, _Step] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            self.steps = list(dict.fromkeys(self.steps))
            self._state = {s: _Step() for s in self.steps}
            history = (load_history(self.history_file or history_path(), self.key)
                       if self.key is not None else {})
            for step in self.steps:
                if step in history:
                    self._rates[step] = history[step]
                    self._from_history.add(step)
                elif step in BUILTIN_RATES:
                    self._rates[step] = BUILTIN_RATES[step]
        except Exception as e:
            logger.warning("time estimate: setup failed (%s)", type(e).__name__)
            self._rates, self._from_history = {}, set()

    # --- what happened -------------------------------------------------------------

    def step_started(self, step: str) -> None:
        try:
            state = self._state.setdefault(step, _Step())
            if state.started is None:
                state.started = self.clock()
        except Exception as e:
            logger.warning("time estimate: step_started failed (%s)", type(e).__name__)

    def step_progress(self, step: str, position: int, total: int) -> None:
        try:
            if total > 0 and 0 <= position <= total:
                self.step_started(step)
                self._state[step].fraction = position / total
        except Exception as e:
            logger.warning("time estimate: step_progress failed (%s)", type(e).__name__)

    def step_finished(self, step: str, seconds: float | None = None, skipped: bool = False) -> None:
        """`seconds`: the measured duration (default: since step_started; 0 if it never started)."""
        try:
            state = self._state.setdefault(step, _Step())
            measured = seconds is not None or state.started is not None
            if seconds is None:
                seconds = self.clock() - state.started if state.started is not None else 0.0
            state.seconds = max(float(seconds), 0.0)
            state.measured = measured
            state.skipped = skipped
            state.fraction = 1.0
        except Exception as e:
            logger.warning("time estimate: step_finished failed (%s)", type(e).__name__)

    # --- estimates -----------------------------------------------------------------

    def _predicted(self, step: str) -> float | None:
        rate = self._rates.get(step)
        return None if rate is None else rate * float(self.duration_s)

    def _usable_duration(self) -> bool:
        return (self.duration_s is not None and math.isfinite(self.duration_s) and self.duration_s > 0)

    def initial(self) -> TimeEstimate:
        """The whole run before it starts."""
        try:
            if not self._usable_duration():
                return NO_ESTIMATE
            known = [s for s in self.steps if s in self._rates]
            if not known:
                return NO_ESTIMATE
            seconds = sum(self._predicted(s) for s in known)
            excludes = tuple(s for s in self.steps if s not in self._rates)
            return TimeEstimate(seconds, self._source(known), excludes)
        except Exception as e:
            logger.warning("time estimate: initial failed (%s)", type(e).__name__)
            return NO_ESTIMATE

    def _source(self, steps: Iterable[str]) -> str:
        return "history" if all(s in self._from_history for s in steps) else "first_run_rough"

    def remaining(self) -> TimeEstimate:
        """Seconds left: the prediction, rescaled by the measured speed after a minute of real work."""
        try:
            if not self._usable_duration():
                return NO_ESTIMATE
            now = self.clock()
            current = next((s for s in self.steps if self._state[s].started is not None
                            and self._state[s].seconds is None), None)
            pending = [s for s in self.steps
                       if self._state[s].started is None and self._state[s].seconds is None]
            in_step = 0.0
            if current is not None:
                in_step = now - self._state[current].started
                if in_step < 0:
                    return NO_ESTIMATE  # the clock went backwards
            # real work so far: finished steps that ran, plus the running step
            done_actual = sum(st.seconds for st in self._state.values() if st.seconds is not None
                              and not st.skipped)
            done_predicted = sum(self._predicted(s) for s, st in self._state.items()
                                 if st.seconds is not None and not st.skipped and s in self._rates)
            real_work = done_actual + in_step
            frac = self._state[current].fraction if current else None
            if current is not None and frac and current in self._rates:
                done_actual += in_step
                done_predicted += self._predicted(current) * frac
            excludes = tuple(s for s in pending + ([current] if current else []) if s not in self._rates)
            known_pending = [s for s in pending if s in self._rates]
            if real_work >= MEASURE_AFTER_S and done_predicted > 0 and done_actual > 0:
                scale = min(max(done_actual / done_predicted, SCALE_LIMITS[0]), SCALE_LIMITS[1])
                source = "measured"
            else:
                scale, source = 1.0, None
            left = sum(self._predicted(s) for s in known_pending) * scale
            if current is not None:
                if frac and 0 < frac < 1:
                    left += in_step * (1 - frac) / frac
                elif current in self._rates:
                    left += max(self._predicted(current) * scale - in_step, 0.0)
            if current is None and not pending:
                return TimeEstimate(0.0, source or self._source(self._rates), ())
            if not known_pending and (current is None or (current not in self._rates and not frac)):
                return TimeEstimate(None, "none", excludes)
            counted = [s for s in [*known_pending, current] if s in self._rates]
            return TimeEstimate(left, source or self._source(counted), excludes)
        except Exception as e:
            logger.warning("time estimate: remaining failed (%s)", type(e).__name__)
            return NO_ESTIMATE

    # --- history -------------------------------------------------------------------

    def measured_rates(self) -> dict[str, float]:
        """{step: seconds per second of video} of the steps that really ran (cached steps left out)."""
        if not self._usable_duration():
            return {}
        rates = {}
        for step, st in self._state.items():
            if st.seconds is None or st.skipped or not st.measured:
                continue
            rate = st.seconds / float(self.duration_s)
            if _valid_rate(rate) and rate > 0:
                rates[step] = rate
        return rates

    def record(self) -> bool:
        """Write this run's measured rates to the history; False (nothing written) if there are none."""
        try:
            if self.key is None:
                return False
            rates = self.measured_rates()
            if not rates:
                return False
            return write_history(self.history_file or history_path(), self.key, rates)
        except Exception as e:
            logger.warning("time estimate: record failed (%s)", type(e).__name__)
            return False
