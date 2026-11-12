"""
The local FastAPI app with the fake core: estimate -> start -> SSE -> finished
in both input modes, and the security checks (Host, token, Origin, open-folder
guard, paths, playlists, one job at a time, SSE replay).

On Windows asyncio's event loop opens a socket pair on 127.0.0.1, so these
tests allow sockets but only connections to 127.0.0.1 (`loopback_only`);
nothing reaches the network.
"""

import json
import socket
from pathlib import Path

import pytest
import pytest_socket
from fastapi.testclient import TestClient

from talk_autopsy.web import dialogs
from talk_autopsy.web.app import TOKEN_HEADER, create_app
from talk_autopsy.web.jobs import JobRunner
from tests.web_fakes import FakeCore

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "test-token-123"
URL = "https://www.youtube.com/watch?v=abc123"


@pytest.fixture
def loopback_only(socket_enabled):
    pytest_socket.socket_allow_hosts(["127.0.0.1"])
    yield


@pytest.fixture
def media(tmp_path):
    path = tmp_path / "talk.mp4"
    path.write_bytes(b"x")
    return path


class Opened(list):
    def __call__(self, path: Path) -> None:
        self.append(path)


@pytest.fixture
def setup(tmp_path, loopback_only):
    core = FakeCore(tmp_path)
    runner = JobRunner(core=core, history_file=tmp_path / "history.json", tick_s=3600)
    opened = Opened()
    picks = []

    def picker(**kw):
        picks.append(kw)
        return dialogs.PickResult(True, str(tmp_path / "talk.mp4"))
    app = create_app(PORT, runner=runner, token=TOKEN, picker=picker, opener=opened,
                     doctor=lambda: [], local_settings=tmp_path / "web.local.json", heartbeat_s=0.0)
    with TestClient(app, base_url=BASE) as client:
        client.headers[TOKEN_HEADER] = TOKEN
        yield client, core, runner, opened, picks


def sse(client, job_id: str, after: int = 0, headers=None) -> tuple[list[dict], list[str]]:
    events, comments = [], []
    with client.stream("GET", f"/api/jobs/{job_id}/events", params={"after": after},
                       headers=headers or {}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
            elif line.startswith(":"):
                comments.append(line)
    return events, comments


def body(mode: str, media: Path | None = None, **kw) -> dict:
    data = {"mode": mode, "target_lang": "ru", "profile": "tv", "domain": "none"}
    data.update({"url": URL} if mode == "url" else {"path": str(media)})
    return {**data, **kw}


# --- happy paths ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["url", "file"])
def test_estimate_start_stream_finished(setup, media, mode):
    client, core, runner, _, _ = setup
    estimate = client.post("/api/estimate", json=body(mode, media))
    assert estimate.status_code == 200, estimate.text
    e = estimate.json()
    assert e["duration_s"] == 600 and e["needs_confirmation"] is False
    assert e["formats"] == ["srt", "mp4-burned"]
    assert e["time"]["first_run_rough"] is True and e["notes"] == []
    assert (e["steps"][0] == "download") == (mode == "url")
    started = client.post("/api/jobs", json=body(mode, media))
    assert started.status_code == 202, started.text
    job = started.json()["job"]
    events, comments = sse(client, job["id"])
    assert [ev["id"] for ev in events] == list(range(1, len(events) + 1))
    assert events[-1]["type"] == "finished"
    assert any(ev["type"] == "step_progress" for ev in events)
    current = client.get("/api/jobs/current").json()["job"]
    assert current["id"] == job["id"] and current["state"] == "finished"
    if mode == "file":
        assert core.calls.count("probe") == 1  # the start reused the estimate


def test_config_lists_languages_profiles_and_strings(setup):
    client = setup[0]
    config = client.get("/api/config", params={"ui": "ru"}).json()
    assert config["languages"]["target"][0]["code"] == "lt"
    assert config["languages"]["target_default"] == "lt"
    assert config["ui_lang"] == "ru" and config["strings"]["tab_url"] == "Ссылка YouTube"
    assert [p["id"] for p in config["profiles"]] == ["tv", "laptop", "all"]
    assert config["cost_limit_usd"] == 3.0 and config["domains"] == ["none", "web3"]


def test_settings_are_remembered(setup, tmp_path):
    client = setup[0]
    assert client.post("/api/settings", json={"target_lang": "ru", "ui_lang": "en"}).json() == {"saved": True}
    config = client.get("/api/config").json()
    assert config["languages"]["target_default"] == "ru" and config["ui_lang"] == "en"
    assert client.post("/api/settings", json={"target_lang": "xx"}).status_code == 422


def test_page_carries_the_token_and_strict_headers(setup):
    client = setup[0]
    page = client.get("/")
    assert page.status_code == 200 and TOKEN in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert "access-control-allow-origin" not in {k.lower() for k in page.headers}
    assert client.get("/static/app.js").status_code == 200


def test_pick_file_returns_the_chosen_path(setup, tmp_path):
    client, _, _, _, picks = setup
    answer = client.post("/api/pick-file", json={"ui_lang": "ru"}).json()
    assert answer == {"available": True, "path": str(tmp_path / "talk.mp4"), "busy": False}
    assert picks[0]["title"] == "Выберите видео"


def test_doctor_gives_names_and_states(setup):
    assert setup[0].get("/api/doctor").json() == {"checks": [], "ok": True}


# --- security ----------------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["evil.example:8765", "127.0.0.1:9999", "localhost", "192.168.1.5:8765"])
def test_wrong_host_is_refused(setup, host):
    client = setup[0]
    for method, path in (("GET", "/"), ("GET", "/api/config"), ("POST", "/api/estimate")):
        response = client.request(method, path, headers={"host": host}, json={})
        assert response.status_code == 400 and response.json()["error"] == "bad_host"


def test_localhost_name_is_accepted(setup):
    assert setup[0].get("/api/config", headers={"host": f"localhost:{PORT}"}).status_code == 200


@pytest.mark.parametrize("path", ["/api/estimate", "/api/jobs", "/api/pick-file", "/api/open-folder",
                                  "/api/settings"])
def test_missing_or_wrong_token_is_refused(setup, media, path):
    client, core, _, opened, picks = setup
    for headers in ({TOKEN_HEADER: ""}, {TOKEN_HEADER: "wrong"}):
        response = client.post(path, json=body("file", media), headers=headers)
        assert response.status_code == 403 and response.json()["error"] == "bad_token"
    assert core.calls == [] and opened == [] and picks == []


def test_cross_origin_post_is_refused(setup, media):
    client, core, _, _, _ = setup
    response = client.post("/api/jobs", json=body("file", media), headers={"origin": "https://evil.example"})
    assert response.status_code == 403 and response.json()["error"] == "cross_origin"
    same = client.post("/api/estimate", json=body("file", media), headers={"origin": BASE})
    assert same.status_code == 200
    assert "pipeline" not in core.calls


def test_cross_origin_get_is_refused_too(setup):
    response = setup[0].get("/api/jobs/current", headers={"origin": "http://localhost:3000"})
    assert response.status_code == 403


def test_open_folder_only_inside_the_output_root(setup, tmp_path):
    client, core, _, opened, _ = setup
    inside = core.output_root() / "A talk"
    inside.mkdir(parents=True)
    assert client.post("/api/open-folder", json={"path": str(inside)}).json() == {"ok": True}
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    for path in (str(outside), str(core.output_root() / ".." / "elsewhere"), str(tmp_path), "C:\\Windows",
                 "/etc", str(core.output_root() / "missing")):
        response = client.post("/api/open-folder", json={"path": path})
        assert response.status_code == 403, path
    assert client.post("/api/open-folder", json={"path": ""}).status_code == 422
    assert client.post("/api/open-folder", json={"path": 5}).status_code == 422
    assert opened == [inside.resolve()]


@pytest.mark.parametrize("path,code", [("talk.mp4", "not_absolute"), ("../talk.mp4", "not_absolute"),
                                       ("", "neither_given")])
def test_invalid_or_relative_path(setup, path, code):
    response = setup[0].post("/api/estimate", json={"mode": "file", "path": path, "target_lang": "ru"})
    assert response.status_code == 422 and response.json()["error"] == code


def test_missing_file_and_bad_extension(setup, tmp_path):
    client = setup[0]
    missing = client.post("/api/estimate", json={"mode": "file", "path": str(tmp_path / "x.mp4"),
                                                 "target_lang": "ru"})
    assert missing.json()["error"] == "no_such_file"
    script = tmp_path / "run.bat"
    script.write_text("echo", encoding="utf-8")
    bad = client.post("/api/jobs", json={"mode": "file", "path": str(script), "target_lang": "ru"})
    assert bad.status_code == 422 and bad.json()["error"] == "bad_extension"


def test_both_or_neither_source(setup, media):
    client = setup[0]
    both = client.post("/api/estimate", json={**body("url"), "path": str(media)})
    assert both.json()["error"] == "both_given"
    neither = client.post("/api/estimate", json={"mode": "url", "target_lang": "ru"})
    assert neither.json()["error"] == "neither_given"
    assert client.post("/api/estimate", json=["not", "an", "object"]).status_code == 422


def test_playlist_url_is_refused(setup):
    client = setup[0]
    response = client.post("/api/estimate", json=body("url", url="https://www.youtube.com/playlist?list=PL1"))
    assert response.status_code == 422 and response.json()["error"] == "playlist"
    assert "launcher" in response.json()["message"]


def test_playlist_found_by_the_metadata_is_refused(setup):
    client, core, _, _, _ = setup
    core.playlist = True
    response = client.post("/api/estimate", json=body("url"))
    assert response.status_code == 422 and response.json()["error"] == "playlist"


def test_second_job_is_refused(setup, media):
    import threading
    client, core, runner, _, _ = setup
    core.gate = threading.Event()
    first = client.post("/api/jobs", json=body("file", media)).json()["job"]
    second = client.post("/api/jobs", json=body("file", media))
    assert second.status_code == 409 and second.json() == {
        "error": "busy", "message": "Another video is being processed; wait until it finishes.",
        "active_id": first["id"]}
    assert client.post("/api/estimate", json=body("file", media)).json()["busy"] is True
    core.gate.set()
    events, _ = sse(client, first["id"])
    assert events[-1]["type"] == "finished"


def test_cost_gate_through_the_api(setup):
    client, core, _, _, _ = setup
    core.duration = 3 * 3600
    amount = client.post("/api/estimate", json=body("url")).json()["cost"]["total_usd"]
    refused = client.post("/api/jobs", json=body("url"))
    assert refused.status_code == 409 and refused.json()["error"] == "confirm_cost"
    assert refused.json()["amount_usd"] == amount
    wrong = client.post("/api/jobs", json=body("url", confirmed_cost_usd=1.0))
    assert wrong.json()["error"] == "cost_mismatch"
    assert client.post("/api/jobs", json=body("url", confirmed_cost_usd=amount)).status_code == 202


def test_sse_sends_a_heartbeat_while_a_job_is_quiet(setup, media):
    import threading
    client, core, _, _, _ = setup
    core.gate = threading.Event()
    job = client.post("/api/jobs", json=body("file", media)).json()["job"]
    threading.Timer(0.8, core.gate.set).start()  # the fake pipeline stays silent until then
    events, comments = sse(client, job["id"])
    assert ": heartbeat" in comments and events[-1]["type"] == "finished"


def test_sse_replay_after_an_id(setup, media):
    client = setup[0]
    job = client.post("/api/jobs", json=body("file", media)).json()["job"]
    everything, _ = sse(client, job["id"])
    later, _ = sse(client, job["id"], after=6)
    assert [e["id"] for e in later] == [e["id"] for e in everything if e["id"] > 6]
    by_header, _ = sse(client, job["id"], headers={"Last-Event-ID": "10"})
    assert by_header[0]["id"] == 11
    assert client.get("/api/jobs/unknown/events").status_code == 404


def test_errors_carry_no_stack_trace(setup, media, monkeypatch):
    client, _, runner, _, _ = setup

    def boom(request):
        raise RuntimeError("secret detail C:\\Users\\someone")
    monkeypatch.setattr(runner, "preview", boom)
    with TestClient(client.app, base_url=BASE, raise_server_exceptions=False) as quiet:
        quiet.headers[TOKEN_HEADER] = TOKEN
        response = quiet.post("/api/estimate", json=body("file", media))
    assert response.status_code == 500
    assert response.json() == {"error": "internal",
                               "message": "Unexpected error; the details are in the log file."}


def test_unknown_routes_are_short_404(setup):
    response = setup[0].get("/api/nothing")
    assert response.status_code == 404 and response.json()["error"] == "not_found"


def test_outside_connections_stay_blocked(loopback_only):
    with pytest.raises(pytest_socket.SocketConnectBlockedError):
        socket.create_connection(("192.0.2.1", 80), timeout=1)
