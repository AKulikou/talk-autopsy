"""Real positions for the download (yt-dlp progress hook) and the burn-in (ffmpeg -progress)."""

import io
from pathlib import Path

import pytest

from talk_autopsy import outputs, youtube
from tests.test_youtube import VIDEO_INFO, fake_ydl  # noqa: F401  (fixture)

# --- download ------------------------------------------------------------------------------


def test_download_progress_spans_the_video_and_audio_parts():
    seen = []
    hook = youtube.DownloadProgress(lambda done, total: seen.append((done, total)))
    info = {"requested_formats": [{"filesize": 900}, {"filesize_approx": 100}]}
    hook({"status": "downloading", "filename": "v.f137.mp4", "downloaded_bytes": 450, "total_bytes": 900,
          "info_dict": info})
    hook({"status": "finished", "filename": "v.f137.mp4", "total_bytes": 900, "info_dict": info})
    hook({"status": "downloading", "filename": "v.f140.m4a", "downloaded_bytes": 50, "total_bytes": 100,
          "info_dict": info})
    hook({"status": "finished", "filename": "v.f140.m4a", "total_bytes": 100, "info_dict": info})
    assert seen == [(450, 1000), (900, 1000), (950, 1000), (1000, 1000)]


def test_download_progress_without_sizes_uses_the_parts_seen():
    seen = []
    hook = youtube.DownloadProgress(lambda done, total: seen.append((done, total)))
    hook({"status": "downloading", "filename": "a", "downloaded_bytes": 10, "total_bytes_estimate": 40})
    hook({"status": "downloading", "filename": "a", "downloaded_bytes": 20})
    hook({"status": "error", "filename": "a"})
    assert seen == [(10, 40), (20, 40)]
    unknown = youtube.DownloadProgress(lambda done, total: seen.append((done, total)))
    unknown({"status": "downloading", "filename": "b", "downloaded_bytes": 5})
    assert seen[-1] == (5, None)


def test_a_broken_display_never_reaches_yt_dlp():
    def broken(done, total):
        raise RuntimeError("display broke")
    youtube.DownloadProgress(broken)({"status": "downloading", "filename": "a", "downloaded_bytes": 1})


def test_download_adds_the_hook_only_when_asked(fake_ydl, tmp_path):  # noqa: F811
    video = youtube.video_from_info(VIDEO_INFO)
    youtube.download(video, tmp_path / "a", ["node"])
    youtube.download(video, tmp_path / "b", ["node"], progress=lambda done, total: None)
    inits = [c[1] for c in fake_ydl.calls if c[0] == "init"]
    assert "progress_hooks" not in inits[0]
    assert isinstance(inits[1]["progress_hooks"][0], youtube.DownloadProgress)


# --- burn-in -------------------------------------------------------------------------------


class FakePopen:
    def __init__(self, lines: list[str], returncode: int = 0, stderr: str = ""):
        self.lines, self.returncode, self.stderr_text = lines, returncode, stderr
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        Path(command[-1]).write_bytes(b"mp4")  # the partial file the real ffmpeg writes
        self.stdout = io.StringIO("".join(f"{line}\n" for line in self.lines))
        self.stderr = io.StringIO(self.stderr_text)
        return self

    def wait(self):
        return self.returncode


def work_with_subtitles(tmp_path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    for name in ("v.ru.srt", "v.en.srt", "v.ru.ass", "v.en.ass"):
        (work / name).write_text("x", encoding="utf-8")
    (tmp_path / "video.mp4").write_bytes(b"video")
    return work


def test_burn_in_reports_the_encoded_position(tmp_path, monkeypatch):
    monkeypatch.setattr(outputs, "_ffmpeg", lambda: "ffmpeg")
    work = work_with_subtitles(tmp_path)
    popen = FakePopen(["frame=10", "out_time_us=N/A", "out_time_us=1500000", "out_time_us=4000000",
                       "out_time_us=99000000", "progress=end"])
    seen = []
    written = outputs.write_outputs(work, "v", "ru", tmp_path / "out", "Talk", ["mp4-burned"],
                                    video=tmp_path / "video.mp4", popen=popen, duration=60.0,
                                    position=lambda done, total: seen.append((done, total)))
    assert seen == [(1.5, 60.0), (4.0, 60.0), (60.0, 60.0)]
    assert [w.format for w in written] == ["mp4-burned"] and written[0].path.name == "Talk.ru.mp4"
    command = popen.commands[0]
    assert command[-4:-1] == ["-progress", "pipe:1", "-nostats"]
    assert command[-1].endswith("Talk.ru.partial.mp4")


def test_burn_in_failure_with_progress_leaves_no_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr(outputs, "_ffmpeg", lambda: "ffmpeg")
    work = work_with_subtitles(tmp_path)
    popen = FakePopen(["out_time_us=1000000"], returncode=1, stderr="Error: libass missing")
    with pytest.raises(outputs.OutputError, match="libass missing"):
        outputs.write_outputs(work, "v", "ru", tmp_path / "out", "Talk", ["mp4-burned"],
                              video=tmp_path / "video.mp4", popen=popen, duration=60.0,
                              position=lambda done, total: None)
    out = tmp_path / "out"
    assert not list(out.glob("*.mp4")) and not (out / outputs.BURN_FILTER_NAME).exists()


def test_with_progress_puts_the_flags_before_the_output():
    assert outputs.with_progress(["ffmpeg", "-i", "a", "b.mp4"]) == [
        "ffmpeg", "-i", "a", "-progress", "pipe:1", "-nostats", "b.mp4"]
