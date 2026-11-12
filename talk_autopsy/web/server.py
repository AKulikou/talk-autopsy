"""
`talk-autopsy web`: the local server for the web page.

Binds 127.0.0.1 only (there is no option to bind elsewhere: the server spends
money and opens folders), on the default port or the next free one, opens the
default browser once the server is up, prints the address, and stops cleanly
on Ctrl+C. The socket is bound here and handed to uvicorn, so the port the
page's Host check expects is the port actually served. Logs go to
logs/web/web-<date>.log (git-ignored); the console shows warnings only. Every
job's events are recorded to logs/web/events/ (git-ignored, the last 20 kept)
for `--replay` (web/replay.py), which plays a recording back with no pipeline,
no network and no cost.
"""

import datetime as dt
import logging
import socket
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

from talk_autopsy import config

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
PORT_TRIES = 20  # default, default + 1, ...; then any free port the system gives

logger = logging.getLogger(__name__)


def bind_socket(preferred: int = DEFAULT_PORT, tries: int = PORT_TRIES) -> socket.socket:
    """A listening-ready socket on 127.0.0.1: the preferred port, the next free one, or any free one."""
    for port in [*range(preferred, preferred + tries), 0]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: no other process may share the port
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind((HOST, port))
            return sock
        except OSError:
            sock.close()
    raise OSError("no free port on 127.0.0.1")


def setup_logging(log_dir: Path | None = None) -> Path:
    log_dir = log_dir or config.LOGS_DIR / "web"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"web-{dt.date.today():%Y%m%d}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console], force=True)
    for noisy in ("httpx", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_file


def make_runner(replay: Path | None, speed: float, record_dir: Path | None):
    """The job runner: a ReplayRunner for `replay`, else the real one recording to `record_dir`."""
    from talk_autopsy.web.jobs import JobRunner
    if replay is not None:
        from talk_autopsy.web.replay import ReplayRunner, load
        return ReplayRunner(load(replay), speed=speed)
    return JobRunner(record_dir=record_dir)


def serve(port: int | None = None, open_browser: bool = True, echo: Callable[[str], None] = print,
          browser_open: Callable[[str], object] = webbrowser.open, server_factory=None,
          replay: Path | None = None, speed: float = 1.0) -> None:
    import uvicorn

    from talk_autopsy.web.app import create_app

    runner = make_runner(replay, speed, config.LOGS_DIR / "web" / "events")  # a bad recording fails first
    log_file = setup_logging()
    sock = bind_socket(port or DEFAULT_PORT)
    actual = sock.getsockname()[1]
    app = create_app(actual, runner=runner)
    url = f"http://{HOST}:{actual}/"
    if port and actual != port:
        echo(f"Port {port} is busy; using {actual}.")
    if replay is not None:
        echo(f"Replay mode: {Path(replay).name} at speed {speed:g} (no pipeline, no network, no cost).")
    echo(f"Talk Autopsy web page: {url}")
    echo(f"Stop with Ctrl+C. Log: {log_file}")
    server = (server_factory or uvicorn.Server)(
        uvicorn.Config(app, log_config=None, access_log=False, lifespan="off"))

    def open_when_ready() -> None:
        for _ in range(100):  # up to 10 s
            if getattr(server, "started", False):
                browser_open(url)
                return
            time.sleep(0.1)
        logger.warning("server not up after 10 s; open %s by hand", url)

    if open_browser:
        threading.Thread(target=open_when_ready, name="open-browser", daemon=True).start()
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()
        if runner.active_id is not None:
            echo("A video was still being processed and has stopped. Finished steps are kept: "
                 "start it again and it continues from them.")
        echo("Stopped.")
