"""Recent results and the in-browser player's files (web/results.py and its endpoints)."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from talk_autopsy.web import results
from talk_autopsy.web.app import TOKEN_HEADER, create_app
from talk_autopsy.web.jobs import RESULT_MARKER, JobRequest, JobRunner
from tests.test_web_app import BASE, PORT, TOKEN, loopback_only  # noqa: F401  (fixture)
from tests.web_fakes import FakeCore

SRT = ("\ufeff1\r\n00:00:01,000 --> 00:00:03,500\r\nПривет\r\n\r\n"
       "2\r\n00:01:02,250 --> 00:01:04,000\r\nДва\r\nстроки\r\n")


def result(root, name="2026-11-12 Talk", files=("Talk.srt", "Talk.en.srt", "Talk.mp4"), marker=None):
    folder = root / name
    folder.mkdir(parents=True)
    for f in files:
        (folder / f).write_text(SRT if f.endswith(".srt") else "media", encoding="utf-8")
    default = {"title": "A talk", "target_lang": "ru", "finished": "2026-11-12T10:00:00"}
    data = default if marker is None else marker
    (folder / RESULT_MARKER).write_text(json.dumps(data), encoding="utf-8")
    return folder


def test_the_original_video_wins_over_the_burned_one(tmp_path):
    folder = result(tmp_path, files=("Talk.srt", "Talk.en.srt", "Talk.mp4", "Talk.ru.mp4", "Talk.subs.mp4"))
    info = results.describe(folder)
    assert (info["media"], info["kind"], info["burned"]) == ("Talk.mp4", "video", False)
    assert [(t["file"], t["srclang"], t["default"]) for t in info["tracks"]] == [
        ("Talk.srt", "ru", True), ("Talk.en.srt", "en", False)]
    assert info["tracks"][0]["label"] == "Русский"
    assert [(f["name"], f["kind"]) for f in info["files"]] == [
        ("Talk.en.srt", "subs_en"), ("Talk.mp4", "original"), ("Talk.ru.mp4", "tv"),
        ("Talk.srt", "subs_target"), ("Talk.subs.mp4", "video_tracks")]  # the marker is not listed


def test_only_a_burned_video_offers_english_only_and_starts_off(tmp_path):
    """The translation is in the picture: its track again drew two copies (owner's report, 2026-11-13)."""
    folder = result(tmp_path, files=("Talk.srt", "Talk.en.srt", "Talk.lt.mp4"), marker={})
    info = results.describe(folder)
    assert (info["media"], info["burned"], info["burned_lang"], info["target_lang"]) == (
        "Talk.lt.mp4", True, "lt", "lt")
    assert [(t["file"], t["srclang"], t["default"]) for t in info["tracks"]] == [("Talk.en.srt", "en", False)]
    assert info["title"] == folder.name
    marked = result(tmp_path, "m", files=("Talk.srt", "Talk.en.srt", "Talk.ru.mp4"),
                    marker={"target_lang": "ru"})
    assert [t["srclang"] for t in results.describe(marked)["tracks"]] == ["en"]
    with_original = result(tmp_path, "o", files=("Talk.mp4", "Talk.srt", "Talk.en.srt", "Talk.ru.mp4"),
                           marker={"target_lang": "ru"})
    info = results.describe(with_original)  # a video without burn-in is preferred, with both tracks
    assert (info["media"], info["burned"], info["burned_lang"]) == ("Talk.mp4", False, "")
    assert [t["srclang"] for t in info["tracks"]] == ["ru", "en"]


def test_audio_and_nothing_to_play(tmp_path):
    audio = results.describe(result(tmp_path, "a", files=("Talk.srt", "Talk.mp3")))
    assert (audio["media"], audio["kind"]) == ("Talk.mp3", "audio")
    none = results.describe(result(tmp_path, "b", files=("Talk.srt",)))
    assert none["media"] is None and none["kind"] is None
    partial = results.describe(result(tmp_path, "c", files=("Talk.srt", "Talk.ru.partial.mp4")))
    assert partial["media"] is None and "Talk.ru.partial.mp4" not in [f["name"] for f in partial["files"]]


@pytest.mark.parametrize("name", ["..", ".", "", "a/b", "a\\b", "x\x00", "missing", "no-marker"])
def test_result_ids_are_one_folder_with_a_marker(tmp_path, name):
    result(tmp_path / "root", "2026-11-12 Talk")
    (tmp_path / "root" / "no-marker").mkdir()
    assert results.folder_of(tmp_path / "root", name) is None
    assert results.folder_of(tmp_path / "root", "2026-11-12 Talk") is not None


def test_file_names_stay_inside_the_result(tmp_path):
    folder = result(tmp_path / "root").resolve()
    (tmp_path / "root" / "secret.mp4").write_text("x", encoding="utf-8")
    for name in ("../secret.mp4", "..", "Talk.mp4/..", "missing.mp4", ""):
        assert results.file_in(folder, name) is None
    assert results.file_in(folder, "Talk.mp4") == folder / "Talk.mp4"


def test_srt_becomes_webvtt():
    vtt = results.srt_to_vtt(SRT)
    assert vtt.startswith("WEBVTT\n\n")
    assert "00:00:01.000 --> 00:00:03.500\nПривет" in vtt
    assert "00:01:02.250 --> 00:01:04.000\nДва\nстроки" in vtt
    assert "\r" not in vtt and "\ufeff" not in vtt and "1\n00:00" not in vtt


def test_recent_is_newest_first_and_bounded(tmp_path, monkeypatch):
    for n in range(5):
        result(tmp_path, f"talk {n}", marker={"title": f"T{n}", "finished": f"2026-11-1{n}T10:00:00"})
    (tmp_path / "not a result").mkdir()
    (tmp_path / "a file.txt").write_text("x", encoding="utf-8")
    assert [r["title"] for r in results.recent(tmp_path, limit=3)] == ["T4", "T3", "T2"]
    monkeypatch.setattr(results, "MAX_SCAN", 2)
    assert len(results.recent(tmp_path)) <= 2
    assert results.recent(tmp_path / "missing") == []


def test_a_cli_result_with_done_json_is_listed(tmp_path):
    folder = tmp_path / "2026-01-05 Fuzz tests"
    folder.mkdir()
    (folder / "Fuzz tests.srt").write_text(SRT, encoding="utf-8")
    (folder / "done.json").write_text(json.dumps({"finished": "2026-11-01T09:00:00"}), encoding="utf-8")
    [info] = results.recent(tmp_path)
    assert info["title"] == "2026-01-05 Fuzz tests" and info["finished"] == "2026-11-01T09:00:00"


@pytest.fixture
def client(tmp_path, loopback_only):  # noqa: F811
    core = FakeCore(tmp_path)
    runner = JobRunner(core=core, history_file=tmp_path / "h.json", tick_s=3600)
    app = create_app(PORT, runner=runner, token=TOKEN, doctor=lambda: [], local_settings=tmp_path / "w.json")
    with TestClient(app, base_url=BASE) as c:
        c.headers[TOKEN_HEADER] = TOKEN
        yield c, core


def test_the_player_endpoints(client):
    c, core = client
    folder = result(core.output_root(), files=("Talk.srt", "Talk.en.srt", "Talk.mp4", "notes.txt"))
    (folder / "Talk.mp4").write_bytes(bytes(range(256)) * 40)
    listed = c.get("/api/results").json()["results"]
    assert [r["id"] for r in listed] == [folder.name]
    rid = folder.name
    info = c.get(f"/api/results/{rid}").json()
    assert info["media"] == "Talk.mp4" and info["folder"] == str(folder)
    whole = c.get(f"/api/results/{rid}/media/Talk.mp4")
    assert whole.status_code == 200 and whole.headers["content-type"] == "video/mp4"
    assert whole.headers["cross-origin-resource-policy"] == "same-origin" and len(whole.content) == 10240
    part = c.get(f"/api/results/{rid}/media/Talk.mp4", headers={"Range": "bytes=256-511"})
    assert part.status_code == 206 and part.headers["content-range"] == "bytes 256-511/10240"
    assert part.content == bytes(range(256))
    vtt = c.get(f"/api/results/{rid}/vtt/Talk.srt")
    assert vtt.status_code == 200 and vtt.text.startswith("WEBVTT")
    assert "text/vtt" in vtt.headers["content-type"]
    for bad in (f"/api/results/{rid}/media/notes.txt", f"/api/results/{rid}/vtt/Talk.mp4",
                f"/api/results/{rid}/media/..%2F..%2Fh.json", "/api/results/..%2Foutput/media/Talk.mp4",
                "/api/results/nope", f"/api/results/{rid}/media/%2E%2E"):
        assert c.get(bad).status_code == 404, bad


def test_other_sites_cannot_load_the_media(client):
    c, core = client
    rid = result(core.output_root()).name
    url = f"/api/results/{rid}/media/Talk.mp4"
    assert c.get(url, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.get(url, headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert c.get(url, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert c.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200  # opening the page is fine


def test_a_finished_job_names_its_result_and_keeps_audio_playable(client, tmp_path):
    c, core = client
    audio = tmp_path / "lecture.mp3"
    audio.write_bytes(b"ID3 audio")
    runner = JobRunner(core=core, history_file=tmp_path / "h2.json", tick_s=3600)
    job = runner.start(JobRequest(mode="file", path=str(audio), profile="laptop", domain="none",
                                  target_lang="ru"))
    deadline = time.monotonic() + 10
    while job.state == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    end = job.events_after(0)[0][-1]
    assert end["type"] == "finished" and end["result"] == "lecture"
    assert ("lecture.mp3", "audio") in [(f["name"], f["purpose"]) for f in end["files"]]
    assert (core.output_root() / "lecture" / "lecture.mp3").read_bytes() == b"ID3 audio"
    marker_file = core.output_root() / "lecture" / ".talk-autopsy" / RESULT_MARKER
    marker = json.loads(marker_file.read_text(encoding="utf-8"))
    assert marker["target_lang"] == "ru" and marker["title"] == "lecture.mp3"
