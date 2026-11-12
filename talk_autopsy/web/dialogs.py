"""
Native dialogs on this machine (the server is local): a file picker and
"open this folder" in the file manager.

The picker runs Tk's file dialog (the native Windows dialog on Windows) in a
separate Python process, so it never blocks the server's event loop or shares
Tk with a worker thread; it gives up after a timeout. Without a display (CI,
a Linux server) it reports "not available" at once, and the page falls back to
a path typed by hand.
"""

import json
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from talk_autopsy.web.sources import MEDIA_EXTENSIONS

logger = logging.getLogger(__name__)

PICK_TIMEOUT_S = 600.0
_PICKER = r"""
import json, sys, tkinter
from tkinter import filedialog
root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)
patterns = " ".join("*" + e for e in json.loads(sys.argv[2]))
path = filedialog.askopenfilename(parent=root, title=sys.argv[1],
                                  filetypes=[(sys.argv[3], patterns), (sys.argv[4], "*.*")])
root.destroy()
print(json.dumps(path or ""))
"""
_lock = threading.Lock()


@dataclass(frozen=True)
class PickResult:
    available: bool  # False: no dialog here; the page shows the path field
    path: str | None = None  # None: cancelled (or nothing chosen)
    busy: bool = False  # another dialog is already open


def display_available(platform: str = sys.platform, env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    if platform.startswith("win") or platform == "darwin":
        return True
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def pick_file(title: str = "Choose a video", media_label: str = "Video or audio",
              all_label: str = "All files", timeout: float = PICK_TIMEOUT_S,
              run=subprocess.run) -> PickResult:
    """The absolute path the person chose, or why there is none. Never raises."""
    if not display_available():
        return PickResult(False)
    if not _lock.acquire(blocking=False):
        return PickResult(True, None, busy=True)
    try:
        result = run([sys.executable, "-I", "-c", _PICKER, title, json.dumps(sorted(MEDIA_EXTENSIONS)),
                      media_label, all_label], capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            logger.warning("file dialog failed (exit %s): %s", result.returncode,
                           (result.stderr or "")[-300:])
            return PickResult(False)
        lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
        chosen = json.loads(lines[-1]) if lines else ""
        return PickResult(True, str(Path(chosen)) if chosen else None)
    except subprocess.TimeoutExpired:
        logger.warning("file dialog closed after %.0f s without an answer", timeout)
        return PickResult(True, None)
    except Exception as e:  # no Tk, a broken display, bad output: the path field is the fallback
        logger.warning("file dialog unavailable (%s)", type(e).__name__)
        return PickResult(False)
    finally:
        _lock.release()


def open_folder(path: Path, platform: str = sys.platform, popen=subprocess.Popen) -> None:
    """Show `path` in the file manager. The caller checks that it is inside the output root."""
    if platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]  # Windows only
    elif platform == "darwin":
        popen(["open", str(path)])
    else:
        popen(["xdg-open", str(path)])
