"""
Service files out of the user's sight (v1.1 session 4): meta.json, done.json and the web page's marker
live in a hidden `.talk-autopsy` folder inside the result folder. Folders made before (the files at the
top) still open, play and appear in the recent results, and are tidied the next time a marker is written.
"""

import json
import os

import pytest

from talk_autopsy import youtube
from talk_autopsy.web import jobs, results

SRT = "1\n00:00:01,000 --> 00:00:02,000\nПривет\n"


def make(folder, layout: str, marker: str = jobs.RESULT_MARKER):
    folder.mkdir(parents=True)
    (folder / "Talk.srt").write_text(SRT, encoding="utf-8")
    (folder / "Talk.en.srt").write_text(SRT, encoding="utf-8")
    (folder / "Talk.mp4").write_bytes(b"video")
    where = folder if layout == "old" else youtube.service_dir(folder, create=True)
    data = {"title": f"{layout} layout", "target_lang": "ru"}
    (where / marker).write_text(json.dumps(data), encoding="utf-8")
    (where / "meta.json").write_text(json.dumps({"id": f"id-{layout}"}), encoding="utf-8")
    return folder


@pytest.mark.parametrize("layout", ["old", "new"])
def test_both_layouts_open_play_and_are_listed(tmp_path, layout):
    folder = make(tmp_path / f"r-{layout}", layout)
    info = results.describe(folder)
    assert info["title"] == f"{layout} layout" and info["media"] == "Talk.mp4" and len(info["tracks"]) == 2
    assert [f["name"] for f in info["files"]] == ["Talk.en.srt", "Talk.mp4", "Talk.srt"]  # no service file
    assert results.folder_of(tmp_path, folder.name) == folder.resolve()
    assert [r["title"] for r in results.recent(tmp_path)] == [f"{layout} layout"]


def test_the_new_layout_shows_only_videos_and_subtitles(tmp_path):
    folder = make(tmp_path / "r", "new")
    youtube.mark_done(folder, ["Talk.srt"], {})
    shown = sorted(p.name for p in folder.iterdir())
    assert shown == [".talk-autopsy", "Talk.en.srt", "Talk.mp4", "Talk.srt"]
    assert youtube.is_done(folder)


def test_writing_a_marker_moves_the_old_service_files_in(tmp_path):
    folder = make(tmp_path / "r", "old")
    youtube.mark_done(folder, ["Talk.srt"], {})  # done.json is new; the old marker and meta.json move in
    jobs.write_result_marker(folder, {"title": "again", "target_lang": "ru"})
    assert sorted(p.name for p in folder.iterdir() if p.is_file()) == ["Talk.en.srt", "Talk.mp4", "Talk.srt"]
    service = folder / youtube.SERVICE_DIR
    assert sorted(p.name for p in service.iterdir()) == ["done.json", "meta.json", jobs.RESULT_MARKER]
    assert results.describe(folder)["title"] == "again"


def test_a_cli_result_with_done_json_in_either_place_is_listed_and_found_by_id(tmp_path):
    make(tmp_path / "a", "old", marker=youtube.DONE_MARKER)
    make(tmp_path / "b", "new", marker=youtube.DONE_MARKER)
    assert youtube.is_done(tmp_path / "a") and youtube.is_done(tmp_path / "b")
    assert youtube._done_by_id(tmp_path, "id-old") and youtube._done_by_id(tmp_path, "id-new")
    assert not youtube._done_by_id(tmp_path, "id-other")
    assert {r["id"] for r in results.recent(tmp_path)} == {"a", "b"}


def test_the_download_writes_meta_json_into_the_service_folder(tmp_path):
    class FakeYdl:
        def __init__(self, params):
            self.params = params

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            (tmp_path / "v" / "Title.mp4").write_bytes(b"video")

    video = youtube.VideoInfo(id="x1", url="https://y/x1", title="Title", upload_date="2026-01-01")
    got = youtube.download(video, tmp_path / "v", ["deno"], ydl_factory=FakeYdl)
    assert got.meta == tmp_path / "v" / ".talk-autopsy" / "meta.json" and got.meta.is_file()
    assert sorted(p.name for p in (tmp_path / "v").iterdir()) == [".talk-autopsy", "Title.mp4"]


@pytest.mark.skipif(os.name != "nt", reason="a Windows attribute (the dot hides it elsewhere)")
def test_the_service_folder_is_hidden_on_windows(tmp_path):
    import ctypes
    service = youtube.service_dir(tmp_path / "r", create=True)
    assert ctypes.windll.kernel32.GetFileAttributesW(str(service)) & 0x2
