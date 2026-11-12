"""The native file dialog degrades to "type the path"; it never raises or hangs the server."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

from talk_autopsy.web import dialogs


def done(stdout="", returncode=0, stderr=""):
    return lambda *a, **kw: SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


def test_display_detection():
    assert dialogs.display_available("win32", {})
    assert dialogs.display_available("darwin", {})
    assert not dialogs.display_available("linux", {})
    assert dialogs.display_available("linux", {"DISPLAY": ":0"})
    assert dialogs.display_available("linux", {"WAYLAND_DISPLAY": "wayland-0"})


def test_no_display_means_not_available(monkeypatch):
    monkeypatch.setattr(dialogs, "display_available", lambda: False)
    called = []
    result = dialogs.pick_file(run=lambda *a, **kw: called.append(1))
    assert result == dialogs.PickResult(False) and called == []


def test_chosen_and_cancelled(monkeypatch, tmp_path):
    monkeypatch.setattr(dialogs, "display_available", lambda: True)
    chosen = tmp_path / "talk.mp4"
    assert dialogs.pick_file(run=done(f'"{chosen.as_posix()}"\n')).path == str(Path(chosen.as_posix()))
    assert dialogs.pick_file(run=done('""\n')) == dialogs.PickResult(True, None)


def test_failures_fall_back_to_the_path_field(monkeypatch):
    monkeypatch.setattr(dialogs, "display_available", lambda: True)
    assert dialogs.pick_file(run=done(returncode=1, stderr="no Tk")).available is False
    assert dialogs.pick_file(run=done("not json")).available is False

    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired("python", 1)
    assert dialogs.pick_file(run=timeout) == dialogs.PickResult(True, None)


def test_open_folder_per_platform(tmp_path):
    calls = []
    dialogs.open_folder(tmp_path, platform="linux", popen=calls.append)
    dialogs.open_folder(tmp_path, platform="darwin", popen=calls.append)
    assert calls == [["xdg-open", str(tmp_path)], ["open", str(tmp_path)]]
