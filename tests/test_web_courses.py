"""
Courses in the web page (web/courses.py): a playlist's lessons, the selection, the
estimate and the cost gate on its total, the order of the lessons, a failure in
the middle, stop after the current lesson, resume, folder names, restart safety
and the course group in the recent results. A fake yt-dlp and a fake pipeline
(tests/web_fakes.py); no network, no API.
"""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from talk_autopsy import pipeline, youtube
from talk_autopsy.web import app as web_app
from talk_autopsy.web import courses, results
from talk_autopsy.web.courses import CourseRequest, CourseRunner
from talk_autopsy.web.jobs import BusyError, GateError, JobRunner
from talk_autopsy.web.sources import InputError, link_kind, normalize_playlist_url
from tests.web_fakes import FakeCore

PLAYLIST = "https://www.youtube.com/playlist?list=PLcourse1"


class PlaylistCore(FakeCore):
    """A playlist of `count` lessons (flat entries), full metadata per lesson, real done markers."""

    def __init__(self, root, count=4, durations=None, private=(), fail_ids=(), **kw):
        super().__init__(root, **kw)
        self.count = count
        self.durations = durations or {}
        self.private = set(private)
        self.fail_ids = set(fail_ids)
        self.ran: list[str] = []
        self.started = threading.Event()
        self.lesson_gate: threading.Event | None = None

    def entry(self, k: int) -> youtube.VideoInfo:
        vid = f"vid{k:02d}"
        if k in self.private:
            return youtube.VideoInfo(id=vid, url=f"https://www.youtube.com/watch?v={vid}",
                                     title="[Private video]", availability="private")
        return youtube.VideoInfo(id=vid, url=f"https://www.youtube.com/watch?v={vid}",
                                 title=f"Lesson {k}: storage, mappings and the Foundry test runner part {k}",
                                 duration=self.durations.get(k, 600.0))

    def fetch(self, url, runtimes):
        self.calls.append("fetch")
        if "list=" in url and "v=" not in url:
            return youtube.PlaylistInfo(id="PLcourse1", title="Solidity: Foundry Fundamentals Course",
                                        entries=[self.entry(k) for k in range(1, self.count + 1)])
        vid = url.split("v=")[1][:5]
        flat = self.entry(int(vid[3:]))
        return youtube.VideoInfo(id=flat.id, url=flat.url, title=flat.title, duration=flat.duration,
                                 description="A lesson.", upload_date="2026-01-02")

    def run_pipeline(self, settings, progress):
        self.ran.append(settings.name)
        self.started.set()
        if self.lesson_gate is not None:
            assert self.lesson_gate.wait(10)
            self.lesson_gate = None
        if settings.name.removeprefix("yt-") in self.fail_ids:
            self.settings = settings
            raise pipeline.PipelineError("the translation step failed (test)")
        return super().run_pipeline(settings, progress)

    def mark_done(self, folder, files, cost):
        super().mark_done(folder, files, cost)
        youtube.mark_done(folder, files, cost)


def make(tmp_path, **kw):
    core = PlaylistCore(tmp_path, **kw)
    runner = JobRunner(core=core, history_file=tmp_path / "history.json", tick_s=3600)
    return CourseRunner(runner, work_root=tmp_path / "work"), core


def req(**kw) -> CourseRequest:
    return CourseRequest.model_validate({"url": PLAYLIST, "target_lang": "ru", **kw})


def wait(course_runner, timeout=20.0):
    end = time.monotonic() + timeout
    while course_runner.active is not None:
        assert time.monotonic() < end, "course did not end"
        time.sleep(0.01)
    return course_runner.current()


# --- links -------------------------------------------------------------------------------------


@pytest.mark.parametrize("url, kind", [
    ("https://www.youtube.com/watch?v=abc123", "video"),
    ("https://www.youtube.com/playlist?list=PLx_1-y", "playlist"),
    ("https://www.youtube.com/watch?v=abc123&list=PLx_1-y&index=3", "video_in_playlist"),
    ("youtu.be/abc123?list=PLx", "video_in_playlist"),
])
def test_link_kinds(url, kind):
    assert link_kind(url) == kind


def test_playlist_links_are_normalized():
    assert normalize_playlist_url("https://www.youtube.com/watch?v=abc123&list=PLx_1&index=3") == \
        "https://www.youtube.com/playlist?list=PLx_1"
    with pytest.raises(InputError) as e:
        normalize_playlist_url("https://www.youtube.com/watch?v=abc123")
    assert e.value.code == "not_playlist"
    with pytest.raises(InputError):
        normalize_playlist_url("https://example.com/playlist?list=PLx")


# --- preview: lessons, unavailable ones, estimate, selection -----------------------------------------


def test_preview_lists_lessons_and_greys_out_unavailable_ones(tmp_path):
    cr, _ = make(tmp_path, count=5, private={3})
    preview = cr.preview(req())
    data = preview.as_dict(None, 3.0)
    assert [x["position"] for x in data["lessons"]] == [1, 2, 3, 4, 5]
    assert [x["available"] for x in data["lessons"]] == [True, True, False, True, True]
    assert data["lessons"][2]["reason"] == "private"
    assert data["selected"] == [1, 2, 4, 5]  # all available ones by default
    assert data["totals"]["count"] == 4 and data["totals"]["ready"] == 0
    one = preview.lessons[0]
    expected = round(4 * (one.claude_usd + one.stt_usd), 2)
    assert data["totals"]["total_usd"] == pytest.approx(expected, abs=0.011)
    assert preview.folder.name.endswith("Solidity Foundry Fundamentals Course")
    assert not preview.folder.exists()
    assert preview.as_dict([2, 3, 9], 3.0)["selected"] == [2]  # an unavailable or unknown lesson never runs


def test_folder_names_sort_and_are_cut_at_a_word(tmp_path):
    cr, _ = make(tmp_path, count=12)
    names = [x.folder_name for x in cr.preview(req()).lessons]
    assert names == sorted(names) and names[0].startswith("01 Lesson 1") and names[11].startswith("12 ")
    assert courses.lesson_folder_name(7, "x", 120).startswith("007 ")
    long_title = "Lesson 1: storage, mappings and the Foundry test runner part 1 " + "word " * 20
    name = courses.lesson_folder_name(1, long_title, 12)
    assert len(name) <= youtube.MAX_NAME_CHARS and name.endswith(" word")  # whole words only


def test_more_than_100_lessons_shows_the_first_100(tmp_path):
    cr, _ = make(tmp_path, count=105)
    preview = cr.preview(req())
    assert len(preview.lessons) == 100 and preview.more == 5


def test_the_total_above_the_limit_needs_the_confirmation(tmp_path):
    cr, core = make(tmp_path, count=4, durations={k: 4 * 3600.0 for k in range(1, 5)})
    preview = cr.preview(req())
    total = preview.totals(None)["total_usd"]
    assert total > 3.0 and preview.needs_confirmation(None, 3.0)
    with pytest.raises(GateError) as e:
        cr.start(req())
    assert e.value.code == "confirm_cost" and e.value.amount_usd == total
    with pytest.raises(GateError) as e:
        cr.start(req(confirmed_cost_usd=total - 1))
    assert e.value.code == "cost_mismatch"
    assert core.ran == []  # nothing ran
    one = preview.totals([1])["total_usd"]
    if one <= 3.0:  # a smaller selection under the limit starts without confirming
        course = cr.start(req(selected=[1]))
        assert wait(cr).state == "finished" and course.summary(3.0)["count"] == 1


def test_confirmed_total_starts(tmp_path):
    cr, core = make(tmp_path, count=2, durations={1: 4 * 3600.0, 2: 4 * 3600.0})
    total = cr.preview(req()).totals(None)["total_usd"]
    cr.start(req(confirmed_cost_usd=total))
    assert wait(cr).state == "finished" and core.ran == ["yt-vid01", "yt-vid02"]


# --- processing ----------------------------------------------------------------------------------------


def test_lessons_run_in_order_into_numbered_folders(tmp_path):
    cr, core = make(tmp_path, count=3)
    course = cr.start(req())
    assert wait(cr).state == "finished"
    assert core.ran == ["yt-vid01", "yt-vid02", "yt-vid03"]
    folders = sorted(p.name for p in course.folder.iterdir() if p.is_dir() and not p.name.startswith("."))
    assert [f[:3] for f in folders] == ["01 ", "02 ", "03 "]
    lesson = course.folder / folders[0]
    assert youtube.is_done(lesson) and any(p.suffix == ".srt" for p in lesson.iterdir())
    marker = courses.read_marker(course.folder)
    assert marker["state"] == "finished" and [x["status"] for x in marker["lessons"]] == ["finished"] * 3
    summary = course.summary(3.0)
    assert summary["finished"] == 3 and summary["failed"] == 0 and summary["lessons"][0]["result"]


def test_a_failed_lesson_does_not_stop_the_course(tmp_path):
    cr, core = make(tmp_path, count=3, fail_ids={"vid02"})
    course = cr.start(req())
    assert wait(cr).state == "finished"
    assert core.ran == ["yt-vid01", "yt-vid02", "yt-vid03"]
    statuses = [x.status for x in course.lessons]
    assert statuses == ["finished", "failed", "finished"]
    error = course.lessons[1].error
    assert error["hint"] == "input" and "translation step failed" in error["reason"]


def test_a_selection_runs_only_the_chosen_lessons(tmp_path):
    cr, core = make(tmp_path, count=5)
    course = cr.start(req(selected=[2, 4]))
    wait(cr)
    assert core.ran == ["yt-vid02", "yt-vid04"]
    assert [x.status for x in course.lessons] == ["not_selected", "finished", "not_selected", "finished",
                                                  "not_selected"]


def test_stop_after_the_current_lesson(tmp_path):
    cr, core = make(tmp_path, count=3)
    core.lesson_gate = threading.Event()
    course = cr.start(req())
    assert core.started.wait(5)
    assert cr.stop_after_current(course.id) and not cr.stop_after_current("other")
    core.lesson_gate.set()
    assert wait(cr).state == "stopped"
    assert core.ran == ["yt-vid01"]
    assert [x.status for x in course.lessons] == ["finished", "queued", "queued"]
    left = courses.unfinished(core.output_root())
    assert len(left) == 1 and left[0]["left"] == [2, 3] and left[0]["done"] == 1


def test_one_job_at_a_time_while_a_course_runs(tmp_path):
    cr, core = make(tmp_path, count=2)
    core.lesson_gate = threading.Event()
    cr.start(req())
    assert core.started.wait(5)
    with pytest.raises(BusyError):
        cr.start(req())
    from talk_autopsy.web.jobs import JobRequest
    with pytest.raises(BusyError):
        cr.runner.start(JobRequest(mode="url", url="https://www.youtube.com/watch?v=zzz111",
                                   target_lang="ru"))
    core.lesson_gate.set()
    wait(cr)


def test_resubmitting_resumes_in_the_same_folders(tmp_path):
    cr, core = make(tmp_path, count=3, fail_ids={"vid03"})
    first = cr.start(req())
    wait(cr)
    core.fail_ids.clear()
    preview = cr.preview(req())
    assert preview.folder == first.folder
    assert [x.ready for x in preview.lessons] == [True, True, False]
    assert [x.folder_name for x in preview.lessons] == [x.folder_name for x in first.lessons]
    second = cr.start(req(selected=[3]))  # "retry the failed ones"
    assert wait(cr).state == "finished" and core.ran[-1] == "yt-vid03"
    assert second.folder == first.folder
    assert len([p for p in tmp_path.joinpath("output").iterdir() if p.is_dir()]) == 1


def test_restart_safety_a_course_left_running_is_unfinished(tmp_path):
    cr, core = make(tmp_path, count=3)
    preview = cr.preview(req())
    course = courses.Course(id="c1", preview=preview, started_wall="2026-11-14T10:00:00")
    for lesson in preview.lessons:
        lesson.status = "queued"
    preview.lessons[0].status = "finished"
    preview.lessons[1].status = "running"  # the server was closed here
    course.folder.mkdir(parents=True)
    course.save()
    left = courses.unfinished(core.output_root())
    assert left[0]["state"] == "running" and left[0]["left"] == [2, 3] and left[0]["url"] == PLAYLIST
    assert courses.unfinished(core.output_root(), running_folder=course.folder) == []


def test_a_lesson_downloaded_before_is_reused(tmp_path):
    cr, core = make(tmp_path, count=1)
    root = core.output_root()
    single = root / "2026-01-02 earlier single run"
    video = core.fetch("https://www.youtube.com/watch?v=vid01", [])
    single.mkdir(parents=True)
    (single / f"{youtube.video_file_stem(video, single)}.mp4").write_bytes(b"downloaded before")
    (youtube.service_dir(single, create=True) / "meta.json").write_text(json.dumps({"id": "vid01"}))
    lesson = root / "x" / "01 lesson"
    courses.reuse_download(root, video, lesson)
    assert (lesson / f"{youtube.video_file_stem(video, lesson)}.mp4").read_bytes() == b"downloaded before"


# --- results: the course as one group, lessons with previous / next -----------------------------------


def test_recent_results_show_the_course_as_one_group(tmp_path):
    cr, core = make(tmp_path, count=3)
    course = cr.start(req())
    wait(cr)
    found = results.recent(core.output_root())
    assert len(found) == 1 and found[0]["group"] == "course" and len(found[0]["lessons"]) == 3
    first, second, third = found[0]["lessons"]
    assert first["id"] == f"{course.folder.name}|{course.lessons[0].folder_name}"
    assert first["course"]["prev"] is None and first["course"]["next"] == second["id"]
    assert third["course"]["next"] is None and second["course"]["number"] == 2
    second_folder = course.folder / course.lessons[1].folder_name
    assert results.folder_of(core.output_root(), second["id"]) == second_folder
    assert results.folder_of(core.output_root(), f"{course.folder.name}|..") is None
    assert results.folder_of(core.output_root(), "a|b|c") is None


@pytest.fixture
def loopback_only(socket_enabled):
    """asyncio's event loop on Windows opens a socket pair on 127.0.0.1 (as in test_web_app.py)."""
    import pytest_socket
    pytest_socket.socket_allow_hosts(["127.0.0.1"])


def test_course_api_end_to_end(tmp_path, loopback_only):
    cr, core = make(tmp_path, count=2)
    app = web_app.create_app(8765, runner=cr.runner, token="t", course_runner=cr)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        _api_checks(client, cr)


def _api_checks(client, cr):
    headers = {"X-Talk-Autopsy-Token": "t"}
    kind = client.post("/api/link", json={"url": PLAYLIST}, headers=headers).json()
    assert kind == {"kind": "playlist"}
    preview = client.post("/api/course/preview", json={"url": PLAYLIST, "target_lang": "ru"}, headers=headers)
    assert preview.status_code == 200 and len(preview.json()["lessons"]) == 2
    started = client.post("/api/course/start", json={"url": PLAYLIST, "target_lang": "ru"}, headers=headers)
    assert started.status_code == 202
    wait(cr)
    current = client.get("/api/course/current").json()["course"]
    assert current["state"] == "finished" and current["finished"] == 2
    assert client.get("/api/course/unfinished").json() == {"courses": []}
    recent = client.get("/api/results").json()["results"]
    assert recent[0]["group"] == "course"
    lesson = client.get(f"/api/results/{recent[0]['lessons'][0]['id']}").json()
    assert lesson["course"]["next"]
    assert client.post("/api/course/stop", json={"id": "nope"}, headers=headers).status_code == 409
