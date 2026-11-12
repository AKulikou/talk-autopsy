"""`talk-autopsy web`: 127.0.0.1 only, a busy port falls back, the browser opens once the server is up."""

import socket
import threading
import time

import pytest
import pytest_socket
import uvicorn
from typer.testing import CliRunner

from talk_autopsy import cli
from talk_autopsy.web import server
from talk_autopsy.web.app import create_app
from talk_autopsy.web.jobs import JobRunner


@pytest.fixture
def loopback_only(socket_enabled):
    pytest_socket.socket_allow_hosts(["127.0.0.1"])
    yield


def test_binds_loopback_and_falls_back_when_busy(loopback_only):
    first = server.bind_socket(0)
    try:
        host, busy = first.getsockname()
        assert host == "127.0.0.1"
        first.listen()
        second = server.bind_socket(busy, tries=3)
        try:
            assert second.getsockname()[0] == "127.0.0.1" and second.getsockname()[1] != busy
        finally:
            second.close()
    finally:
        first.close()


class FakeServer:
    def __init__(self, config):
        self.config = config
        self.started = False
        self.sockets = None

    def run(self, sockets=None):
        self.sockets = sockets
        self.started = True
        import time
        time.sleep(0.3)  # the browser thread sees `started`


def test_serve_prints_the_address_and_opens_the_browser(loopback_only, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "setup_logging", lambda: tmp_path / "web.log")
    lines, opened, made = [], [], []

    def factory(config):
        made.append(FakeServer(config))
        return made[0]
    server.serve(0, echo=lines.append, browser_open=opened.append, server_factory=factory)
    sock = made[0].sockets[0]
    assert isinstance(sock, socket.socket) and sock.fileno() == -1  # closed after the stop
    url = lines[0 if lines[0].startswith("Talk") else 1].split(": ", 1)[1]
    assert url.startswith("http://127.0.0.1:") and opened == [url]
    assert lines[-1] == "Stopped."


def test_no_browser_option(loopback_only, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "setup_logging", lambda: tmp_path / "web.log")
    opened = []
    server.serve(0, open_browser=False, echo=lambda line: None, browser_open=opened.append,
                 server_factory=FakeServer)
    assert opened == []


def test_cli_registers_the_command(monkeypatch):
    seen = {}
    monkeypatch.setattr(server, "serve", lambda port, open_browser, echo, replay, speed, reuse: seen.update(
        port=port, browser=open_browser, replay=replay, speed=speed, reuse=reuse))
    result = CliRunner().invoke(cli.app, ["web", "--port", "9100", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert seen == {"port": 9100, "browser": False, "replay": None, "speed": 1.0, "reuse": True}
    result = CliRunner().invoke(cli.app, ["web", "--replay", "x.jsonl", "--speed", "0"])
    assert result.exit_code == 0 and seen["replay"].name == "x.jsonl" and seen["speed"] == 0.0
    assert seen["reuse"] is False  # a replay never opens the real page instead
    assert "--replay" not in CliRunner().invoke(cli.app, ["web", "--help"]).output  # hidden


def test_a_bad_recording_is_one_line(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{}", encoding="utf-8")
    result = CliRunner().invoke(cli.app, ["web", "--replay", str(bad), "--no-browser"])
    assert result.exit_code == 2 and "no recording header" in result.output


def test_logging_goes_to_a_file(tmp_path, monkeypatch):
    import logging
    seen = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: seen.update(kw))  # the real root stays as it is
    log_file = server.setup_logging(tmp_path / "logs")
    file_handler, console = seen["handlers"]
    try:
        file_handler.handle(logging.makeLogRecord({"msg": "hello", "levelno": logging.INFO, "name": "x"}))
        file_handler.flush()
        assert log_file.parent == tmp_path / "logs" and "hello" in log_file.read_text(encoding="utf-8")
        assert console.level == logging.WARNING and seen["level"] == logging.INFO
    finally:
        file_handler.close()


def test_a_second_start_opens_the_running_page(loopback_only, tmp_path, monkeypatch):
    sock = server.bind_socket(0)
    port = sock.getsockname()[1]
    app = create_app(port, runner=JobRunner(history_file=tmp_path / "h.json"))
    first = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="off"))
    thread = threading.Thread(target=first.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if first.started:
                break
            time.sleep(0.05)
        assert server.running_instance(port, tries=1) == f"http://127.0.0.1:{port}/"
        lines, opened = [], []
        monkeypatch.setattr(server, "setup_logging", lambda: tmp_path / "web.log")
        server.serve(port, echo=lines.append, browser_open=opened.append, reuse=True,
                     server_factory=lambda config: pytest.fail("a second server was started"))
        assert opened == [f"http://127.0.0.1:{port}/"] and "already running" in lines[0]
    finally:
        first.should_exit = True
        thread.join(5)
        sock.close()
    assert server.running_instance(port, tries=1) is None


def test_a_dropped_browser_connection_is_not_an_error():
    import logging
    gone = server.ClientGone()
    try:
        raise ConnectionResetError(10054, "closed by the remote host")
    except ConnectionResetError:
        import sys
        error = sys.exc_info()
    record = logging.LogRecord("asyncio", logging.ERROR, "x", 1, "Exception in callback", None, error)
    assert gone.filter(record) is False
    assert gone.filter(logging.LogRecord("asyncio", logging.ERROR, "x", 1, "other", None, None)) is True
