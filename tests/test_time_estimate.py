"""
The machine-local time estimate never blocks a run: every failure is "no
estimate", history is per machine key, written atomically, and cached steps
never reach it.
"""

import json
from pathlib import Path, PureWindowsPath

import pytest

from talk_autopsy import time_estimate as te
from talk_autopsy.time_estimate import BUILTIN_RATES, MachineKey, TimeEstimator

KEY = MachineKey("assemblyai", "claude-sonnet-5-5", "cloud", "cpu", 8)
OTHER = MachineKey("assemblyai", "claude-sonnet-5-5", "cloud", "cpu", 4)
STEPS = ["stt", "glossary", "stt2", "align", "fix", "translate", "srt"]
HOUR = 3600.0


class Clock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def estimator(tmp_path: Path, steps=STEPS, duration=HOUR, key=KEY, clock=None) -> TimeEstimator:
    return TimeEstimator(key, duration, list(steps), history_file=tmp_path / "history.json",
                         clock=clock or Clock())


def builtin_seconds(steps, duration=HOUR) -> float:
    return sum(BUILTIN_RATES[s] for s in steps) * duration


# --- sources ------------------------------------------------------------------------


def test_no_history_gives_the_built_in_rough_estimate(tmp_path):
    e = estimator(tmp_path).initial()
    assert e.available and e.first_run_rough and e.source == "first_run_rough"
    assert e.seconds == pytest.approx(builtin_seconds(STEPS))
    assert 9 * 60 < e.seconds < 11 * 60  # about 10 min per hour of video, as measured
    assert e.excludes == ()


def test_download_and_soft_mp4_have_no_built_in_value(tmp_path):
    e = estimator(tmp_path, steps=["download", *STEPS, "outputs"]).initial()
    assert e.seconds == pytest.approx(builtin_seconds(STEPS))
    assert e.excludes == ("download", "outputs")


def test_corrupted_history_is_no_history(tmp_path):
    (tmp_path / "history.json").write_text("{not json", encoding="utf-8")
    e = estimator(tmp_path).initial()
    assert e.first_run_rough and e.seconds == pytest.approx(builtin_seconds(STEPS))


@pytest.mark.parametrize("content", ['[]', '{"machines": []}', '{"machines": {"x": 1}}',
                                     '{"machines": {"assemblyai|claude-sonnet-5-5|cloud|cpu|8": '
                                     '{"rates": {"fix": ["a", -1, null, true]}}}}'])
def test_history_with_wrong_types_is_ignored(tmp_path, content):
    (tmp_path / "history.json").write_text(content, encoding="utf-8")
    assert estimator(tmp_path).initial().first_run_rough


def test_history_of_another_machine_key_is_not_used(tmp_path):
    other = estimator(tmp_path, key=OTHER)
    for step in STEPS:
        other.step_finished(step, seconds=10.0)
    assert other.record()
    e = estimator(tmp_path).initial()
    assert e.first_run_rough and e.seconds == pytest.approx(builtin_seconds(STEPS))
    assert estimator(tmp_path, key=OTHER).initial().source == "history"


def test_history_of_this_machine_is_used(tmp_path):
    first = estimator(tmp_path)
    for step in STEPS:
        first.step_finished(step, seconds=36.0)  # 0.01 s per second of video
    assert first.record()
    e = estimator(tmp_path).initial()
    assert e.source == "history" and not e.first_run_rough
    assert e.seconds == pytest.approx(36.0 * len(STEPS))


def test_partial_history_is_flagged_rough(tmp_path):
    first = estimator(tmp_path, steps=["fix"])
    first.step_finished("fix", seconds=36.0)
    first.record()
    e = estimator(tmp_path).initial()
    assert e.first_run_rough
    assert e.seconds == pytest.approx(builtin_seconds([s for s in STEPS if s != "fix"]) + 36.0)


@pytest.mark.parametrize("duration", [None, 0.0, -5.0, float("nan"), float("inf")])
def test_unusable_duration_gives_no_estimate(tmp_path, duration):
    e = estimator(tmp_path, duration=duration)
    assert e.initial() == te.NO_ESTIMATE
    assert e.remaining() == te.NO_ESTIMATE
    assert not e.initial().available and e.initial().as_dict()["seconds"] is None


def test_no_known_step_gives_no_estimate(tmp_path):
    assert estimator(tmp_path, steps=["download"]).initial() == te.NO_ESTIMATE


def test_unknown_key_gives_the_built_in_values(tmp_path):
    assert estimator(tmp_path, key=None).initial().first_run_rough


# --- recalculation ------------------------------------------------------------------


def test_before_the_first_minute_the_prediction_counts_down(tmp_path):
    clock = Clock()
    e = estimator(tmp_path, clock=clock)
    e.step_started("stt")
    clock.now += 30
    left = e.remaining()
    assert left.first_run_rough
    assert left.seconds == pytest.approx(builtin_seconds(STEPS) - 30)


def test_after_the_first_minute_the_measured_speed_rescales(tmp_path):
    clock = Clock()
    e = estimator(tmp_path, clock=clock)
    predicted_stt = BUILTIN_RATES["stt"] * HOUR  # 92 s
    e.step_started("stt")
    clock.now += 2 * predicted_stt  # this machine is twice as slow
    e.step_finished("stt")
    e.step_started("glossary")
    left = e.remaining()
    assert left.source == "measured"
    rest = builtin_seconds([s for s in STEPS if s != "stt"])
    assert left.seconds == pytest.approx(2 * rest)


def test_a_real_position_extrapolates_the_running_step(tmp_path):
    clock = Clock()
    e = estimator(tmp_path, steps=["fix", "translate"], clock=clock)
    e.step_started("fix")
    clock.now += 100
    e.step_progress("fix", 1, 4)  # a quarter done in 100 s: 300 s left in this step
    left = e.remaining()
    assert left.source == "measured"
    scale = 100 / (BUILTIN_RATES["fix"] * HOUR / 4)
    assert left.seconds == pytest.approx(300 + BUILTIN_RATES["translate"] * HOUR * scale)


def test_scale_is_clamped(tmp_path):
    clock = Clock()
    e = estimator(tmp_path, steps=["stt", "fix"], clock=clock)
    e.step_started("stt")
    clock.now += 100 * BUILTIN_RATES["stt"] * HOUR
    e.step_finished("stt")
    assert e.remaining().seconds == pytest.approx(te.SCALE_LIMITS[1] * BUILTIN_RATES["fix"] * HOUR)


def test_cached_steps_do_not_count_as_measured_speed(tmp_path):
    clock = Clock()
    e = estimator(tmp_path, clock=clock)
    for step in ("stt", "glossary", "stt2"):
        e.step_finished(step, seconds=0.01, skipped=True)
    left = e.remaining()
    assert left.first_run_rough
    assert left.seconds == pytest.approx(builtin_seconds(["align", "fix", "translate", "srt"]))


def test_all_done_leaves_zero(tmp_path):
    e = estimator(tmp_path, steps=["srt"])
    e.step_finished("srt", seconds=1.0)
    assert e.remaining().seconds == 0.0


def test_a_clock_going_backwards_gives_no_estimate(tmp_path):
    clock = Clock()
    e = estimator(tmp_path, clock=clock)
    e.step_started("stt")
    clock.now -= 50
    assert e.remaining() == te.NO_ESTIMATE


def test_an_exception_inside_gives_no_estimate(tmp_path):
    def broken_clock():
        raise OSError("clock unavailable")
    e = estimator(tmp_path, clock=broken_clock)
    e.step_started("stt")  # swallowed
    assert e.remaining() == te.NO_ESTIMATE
    e.step_finished("stt")  # swallowed
    assert e.record() is False  # nothing measured; never raises


def test_an_exception_while_loading_gives_no_estimate(tmp_path, monkeypatch):
    def boom(*args):
        raise RuntimeError("bad")
    monkeypatch.setattr(te, "load_history", boom)
    assert estimator(tmp_path).initial() == te.NO_ESTIMATE


# --- history writing ----------------------------------------------------------------


def test_history_write_is_atomic_and_keeps_the_last_five(tmp_path, monkeypatch):
    replaced = []
    real_replace = te.os.replace

    def spy(src, dst):
        replaced.append((Path(src).name, Path(dst).name))
        real_replace(src, dst)
    monkeypatch.setattr(te.os, "replace", spy)
    for seconds in (10, 20, 30, 40, 50, 60, 70):
        e = estimator(tmp_path, steps=["fix"])
        e.step_finished("fix", seconds=float(seconds))
        assert e.record()
    assert all(src.endswith(".tmp") and dst == "history.json" for src, dst in replaced)
    data = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    rates = data["machines"][KEY.text()]["rates"]["fix"]
    assert len(rates) == te.KEEP_RATES and rates[-1] == pytest.approx(70 / HOUR)
    assert not list(tmp_path.glob("*.tmp"))


def test_a_failed_write_is_logged_and_ignored(tmp_path, monkeypatch, caplog):
    def fail(src, dst):
        raise PermissionError("locked")
    monkeypatch.setattr(te.os, "replace", fail)
    e = estimator(tmp_path, steps=["fix"])
    e.step_finished("fix", seconds=10.0)
    assert e.record() is False
    assert "not written" in caplog.text
    assert not list(tmp_path.glob("*.tmp"))  # the temporary file is cleaned up


def test_a_corrupted_file_is_replaced_on_write(tmp_path):
    (tmp_path / "history.json").write_text("garbage", encoding="utf-8")
    e = estimator(tmp_path, steps=["fix"])
    e.step_finished("fix", seconds=36.0)
    assert e.record()
    assert estimator(tmp_path, steps=["fix"]).initial().source == "history"


def test_a_cached_run_is_not_recorded(tmp_path):
    e = estimator(tmp_path)
    for step in STEPS:
        e.step_finished(step, seconds=0.02, skipped=True)
    assert e.record() is False
    assert not (tmp_path / "history.json").exists()


def test_cached_steps_of_a_partly_cached_run_are_left_out(tmp_path):
    e = estimator(tmp_path, steps=["stt", "fix"])
    e.step_finished("stt", seconds=0.02, skipped=True)
    e.step_finished("fix", seconds=90.0)
    assert e.record()
    data = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    rates = data["machines"][KEY.text()]["rates"]
    assert set(rates) == {"fix"}


# --- cache directory on each platform -------------------------------------------------


def test_cache_dir_on_windows():
    path = te.user_cache_dir("win32", {"LOCALAPPDATA": r"C:\Users\someone\AppData\Local"},
                             home=Path("/home/someone"))
    assert PureWindowsPath(str(path)).parts[-1] == "talk-autopsy"
    assert str(path).replace("\\", "/").startswith("C:/Users/someone/AppData/Local")


def test_cache_dir_on_windows_without_localappdata():
    path = te.user_cache_dir("win32", {}, home=Path("/h"))
    assert path == Path("/h") / "AppData" / "Local" / "talk-autopsy"


def test_cache_dir_on_linux():
    assert te.user_cache_dir("linux", {}, home=Path("/h")) == Path("/h/.cache/talk-autopsy")
    assert te.user_cache_dir("linux", {"XDG_CACHE_HOME": "/x"}, home=Path("/h")) == Path("/x/talk-autopsy")


def test_cache_dir_on_macos():
    assert te.user_cache_dir("darwin", {}, home=Path("/h")) == Path("/h/Library/Caches/talk-autopsy")


def test_a_step_without_a_prediction_does_not_skew_the_measured_speed(tmp_path):
    # smoke 2 (2026-11-12): a 17.5 s download without history made the rest look twice as slow
    clock = Clock()
    e = estimator(tmp_path, steps=["download", "stt", "fix"], clock=clock)
    e.step_started("download")
    clock.now += 50
    e.step_finished("download")
    e.step_started("stt")
    clock.now += BUILTIN_RATES["stt"] * HOUR  # exactly as predicted
    e.step_finished("stt")
    left = e.remaining()
    assert left.source == "measured"
    assert left.seconds == pytest.approx(BUILTIN_RATES["fix"] * HOUR)
