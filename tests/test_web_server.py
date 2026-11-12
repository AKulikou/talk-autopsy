"""`talk-autopsy web`: 127.0.0.1 only, a busy port falls back, the browser opens once the server is up."""

import socket

import pytest
import pytest_socket
from typer.testing import CliRunner

from talk_autopsy import cli
from talk_autopsy.web import server


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
    monkeypatch.setattr(server, "serve", lambda port, open_browser, echo: seen.update(port=port,
                                                                                         browser=open_browser))
    result = CliRunner().invoke(cli.app, ["web", "--port", "9100", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert seen == {"port": 9100, "browser": False}


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
