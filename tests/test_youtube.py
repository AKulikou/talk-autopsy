"""YouTube input with a fake yt-dlp: metadata, estimate, download order, context, playlists, doctor."""

import datetime as dt
import functools
import json

import pytest
from typer.testing import CliRunner

from talk_autopsy import cli, config, pipeline, youtube

runner = CliRunner()

VIDEO_INFO = {
    "id": "abc123", "title": "Foundry: fuzz tests | part 1?", "channel": "Some Channel", "duration": 1800,
    "description": "We test ERC-20 tokens with forge.", "upload_date": "20260105",
    "chapters": [{"title": "Intro"}, {"title": "Invariant tests"}],
    "subtitles": {"en": [{}], "de": [{}]}, "automatic_captions": {"en": [{}]},
    "webpage_url": "https://www.youtube.com/watch?v=abc123",
}


class FakeYDL:
    """Records every call; `download` writes the MP4 (and a caption file) where yt-dlp would."""

    calls: list = []
    infos: dict = {}

    def __init__(self, params):
        self.params = params
        FakeYDL.calls.append(("init", params))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        FakeYDL.calls.append(("extract_info", url, download))
        return FakeYDL.infos[url]

    def download(self, urls):
        FakeYDL.calls.append(("download", urls))
        template = self.params["outtmpl"]["default"]
        from pathlib import Path
        Path(template.replace("%(ext)s", "mp4")).write_bytes(b"fake mp4")
        if self.params["writesubtitles"]:
            Path(self.params["outtmpl"]["subtitle"].replace("%(ext)s", "en.srt")).write_text("1\n", "utf-8")
        return 0


@pytest.fixture
def fake_ydl(monkeypatch):
    FakeYDL.calls = []
    FakeYDL.infos = {VIDEO_INFO["webpage_url"]: VIDEO_INFO}
    monkeypatch.setattr(youtube, "_default_factory", FakeYDL)
    monkeypatch.setattr(youtube, "find_js_runtimes", lambda classes=None: [("node", "24.15.0")])
    monkeypatch.setattr(youtube, "ejs_installed", lambda: True)
    return FakeYDL


def test_metadata_without_download(fake_ydl):
    info = youtube.fetch(VIDEO_INFO["webpage_url"], ["node"])
    assert isinstance(info, youtube.VideoInfo)
    assert info.title.startswith("Foundry") and info.duration == 1800 and info.upload_date == "2026-01-05"
    assert info.chapters == ["Intro", "Invariant tests"] and info.author_captions == ["de", "en"]
    init = fake_ydl.calls[0][1]
    assert init["skip_download"] is True and init["js_runtimes"] == {"node": {}}
    assert ("extract_info", VIDEO_INFO["webpage_url"], False) in fake_ydl.calls
    assert not [c for c in fake_ydl.calls if c[0] == "download"]


def test_context_has_title_description_and_chapters():
    video = youtube.video_from_info(VIDEO_INFO)
    context = video.context()
    assert "Foundry: fuzz tests" in context and "ERC-20" in context and "- Invariant tests" in context


def test_estimate_from_the_measured_rates():
    e = youtube.estimate(3600)
    assert e.claude_usd == config.RUN_CLAUDE_USD_PER_HOUR and e.stt_usd == config.RUN_STT_USD_PER_HOUR
    assert e.pipeline_minutes == config.RUN_MINUTES_PER_HOUR
    assert youtube.estimate(None).minutes is None


def test_download_writes_meta_first_and_keeps_author_captions(fake_ydl, tmp_path):
    video = youtube.video_from_info(VIDEO_INFO)
    folder = tmp_path / youtube.video_folder_name(video)
    result = youtube.download(video, folder, ["node"])
    assert result.video.name == "Foundry fuzz tests part 1.mp4" and result.video.is_file()
    assert [c.name for c in result.captions] == ["Foundry fuzz tests part 1.youtube.en.srt"]
    assert json.loads(result.meta.read_text(encoding="utf-8"))["id"] == "abc123"
    params = [c[1] for c in fake_ydl.calls if c[0] == "init"][-1]
    assert params["merge_output_format"] == "mp4" and "height<=1080" in params["format"]
    assert params["subtitleslangs"] == ["en"] and params["writeautomaticsub"] is False


def test_download_reuses_an_existing_mp4(fake_ydl, tmp_path):
    video = youtube.video_from_info(VIDEO_INFO)
    folder = tmp_path / "v"
    youtube.download(video, folder, ["node"])
    youtube.download(video, folder, ["node"])
    assert len([c for c in fake_ydl.calls if c[0] == "download"]) == 1


def test_yt_dlp_failure_gives_the_hint(monkeypatch, tmp_path):
    from yt_dlp.utils import DownloadError

    class Broken(FakeYDL):
        def extract_info(self, url, download=False):
            raise DownloadError("Sign in to confirm")

    monkeypatch.setattr(youtube, "_default_factory", Broken)
    with pytest.raises(youtube.YouTubeError) as e:
        youtube.fetch("https://www.youtube.com/watch?v=x", ["node"])
    assert "talk-autopsy doctor" in str(e.value) and "run <file>" in str(e.value)


@pytest.mark.parametrize(("title", "expected"), [
    ('a/b\\c:d*e?f"g<h>i|j', "a b c d e f g h i j"),
    ("ends with dots...", "ends with dots"),
    ("CON", "_CON"),
    ("   ", "video"),
    ("x" * 200, "x" * 80),
])
def test_windows_safe_names(title, expected):
    assert youtube.safe_name(title) == expected


def test_folder_name_has_the_upload_date():
    assert youtube.video_folder_name(youtube.video_from_info(VIDEO_INFO)) == \
        "2026-01-05 Foundry fuzz tests part 1"


def _playlist(durations):
    entries = [youtube.VideoInfo(id=f"v{i}", url=f"https://y/{i}", title=f"Lesson {i}", duration=d)
               for i, d in enumerate(durations, start=1)]
    return youtube.PlaylistInfo(id="pl", title="Course", entries=entries)


def test_playlist_order_cap_and_max_videos(tmp_path):
    playlist = _playlist([3600, 3600, 3600, 3600])
    plan = youtube.plan_playlist(playlist, tmp_path, max_videos=3, max_total_usd=2.7,
                                 max_video_usd=3.0)
    assert [v.id for v in plan.todo] == ["v1", "v2"]  # 1.29 USD each; the third would cross 2.7
    assert [v.id for v in plan.over_cap] == ["v3"]  # max_videos=3: v4 is not considered


def test_playlist_skips_finished_videos(tmp_path):
    playlist = _playlist([600, 600])
    folder = tmp_path / "2026-01-01 Lesson 1"
    folder.mkdir()
    (folder / "meta.json").write_text(json.dumps({"id": "v1"}), encoding="utf-8")
    youtube.mark_done(folder, ["x.srt"], {})
    plan = youtube.plan_playlist(playlist, tmp_path, None, 10.0, 3.0)
    assert [v.id for v in plan.skipped_done] == ["v1"] and [v.id for v in plan.todo] == ["v2"]


def test_playlist_stops_before_a_video_without_duration(tmp_path):
    plan = youtube.plan_playlist(_playlist([600, None, 600]), tmp_path, None, 10.0, 3.0)
    assert [v.id for v in plan.todo] == ["v1"] and [v.id for v in plan.over_cap] == ["v2", "v3"]


def test_missing_javascript_runtime_message():
    with pytest.raises(youtube.YouTubeError) as e:
        youtube.require_youtube_tools(runtimes=[], ejs=True)
    assert "winget install DenoLand.Deno" in str(e.value)


def test_doctor_reports_names_only():
    checks = youtube.doctor(which=lambda name: None, runtimes=[], version="2026.09.01", ejs=False,
                            key_is_set=lambda name: name == "ANTHROPIC_API_KEY", today=dt.date(2026, 11, 10),
                            align=False)
    by_name = {c.name: c for c in checks}
    align = by_name["align extra (Gemini fallback only)"]
    assert not align.ok and not align.required and align.fix == "uv sync --extra align"
    assert "Gemini fallback" in align.detail
    assert not by_name["ffmpeg"].ok and not by_name["JavaScript runtime (YouTube)"].ok
    assert by_name["yt-dlp version"].ok is False and "70 days" in by_name["yt-dlp version"].detail
    assert "uv lock --upgrade-package yt-dlp" in by_name["yt-dlp version"].fix
    assert by_name["ANTHROPIC_API_KEY"].ok and by_name["ASSEMBLYAI_API_KEY"].ok is False
    assert {c.detail for c in checks if c.name.endswith("_KEY")} <= {"set", "not set"}


def test_doctor_reports_whether_the_web_ui_can_start():
    def checks(web):
        found = youtube.doctor(which=lambda name: "/bin/x", runtimes=[("deno", "2")], version="2026.11.01",
                               ejs=True, align=True, key_is_set=lambda name: True,
                               today=dt.date(2026, 11, 12), web=web)
        return {c.name: c for c in found}["web UI (talk-autopsy web)"]
    assert checks(True).ok and "can start" in checks(True).detail
    missing = checks(False)
    assert not missing.ok and not missing.required and missing.fix == "uv sync"
    assert youtube.web_ui_installed()  # a regular dependency: installed by a plain uv sync


def test_cli_youtube_run_passes_context_and_writes_outputs(fake_ydl, monkeypatch, tmp_path):
    seen = {}

    def fake_run(settings, progress=None):
        seen["settings"] = settings
        work = settings.work_dir
        work.mkdir(parents=True, exist_ok=True)
        for suffix in ("ru", "en"):
            (work / f"{settings.video_id}.{suffix}.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n",
                                                                     encoding="utf-8")
            (work / f"{settings.video_id}.{suffix}.ass").write_text("[Script Info]\n", encoding="utf-8")
        return pipeline.RunReport()

    monkeypatch.setattr(pipeline, "run", fake_run)
    settings_in_tmp = functools.partial(pipeline.RunSettings, work_root=tmp_path / "work")
    monkeypatch.setattr(pipeline, "RunSettings", settings_in_tmp)
    result = runner.invoke(cli.app, ["run", VIDEO_INFO["webpage_url"], "--output", "srt"])
    assert result.exit_code == 0, result.output
    settings = seen["settings"]
    assert "Invariant tests" in settings.context and "ERC-20" in settings.context
    assert settings.name == "yt-abc123" and settings.source.name.endswith(".mp4")
    folder = tmp_path / "output" / "2026-01-05 Foundry fuzz tests part 1"
    assert (folder / "Foundry fuzz tests part 1.srt").is_file()  # same name as the video: TVs auto-load it
    assert youtube.is_done(folder)
    order = [c[0] for c in fake_ydl.calls]
    assert order.index("extract_info") < order.index("download")
    assert str(settings.work_dir).startswith(str(tmp_path))


def test_cli_dry_run_downloads_nothing(fake_ydl):
    result = runner.invoke(cli.app, ["run", VIDEO_INFO["webpage_url"], "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "30.0 min of video" in result.output and "author's captions: de, en" in result.output
    assert not [c for c in fake_ydl.calls if c[0] == "download"]


def test_cli_doctor_reports_the_align_extra_as_a_warning_with_the_command(monkeypatch):
    real = youtube.doctor

    def probed(**_):
        return real(which=lambda name: f"/bin/{name}", runtimes=[("node", "24.15.0")], version="2026.11.01",
                    ejs=True, key_is_set=lambda name: True, today=dt.date(2026, 11, 11), align=False)
    monkeypatch.setattr(youtube, "doctor", probed)
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0, result.output  # optional: a warning, not a failure
    assert "warning  align extra (Gemini fallback only): not installed" in result.output
    assert "fix: uv sync --extra align" in result.output
    monkeypatch.setattr(youtube, "doctor", lambda **_: real(
        which=lambda name: f"/bin/{name}", runtimes=[("node", "24.15.0")], version="2026.11.01", ejs=True,
        key_is_set=lambda name: True, today=dt.date(2026, 11, 11), align=True))
    installed = runner.invoke(cli.app, ["doctor"])
    assert "ok       align extra (Gemini fallback only): installed" in installed.output
    assert "--extra align" not in installed.output


# --- one per-video cost limit (v1.1): Claude + speech recognition, CLI and web alike ---------

THREE_HOURS = 3 * 3600  # Claude 2.46 USD (under 3), Claude + AssemblyAI 3.87 USD (over 3)


def test_the_per_video_limit_counts_claude_and_speech_recognition():
    e = youtube.estimate(THREE_HOURS)
    assert e.claude_usd <= config.COST_CONFIRMATION_USD < e.total_usd
    assert youtube.over_video_limit(e, config.COST_CONFIRMATION_USD)
    assert not youtube.over_video_limit(e, 4.0)
    assert not youtube.over_video_limit(youtube.estimate(None), 0.0)  # unknown: the caller decides


def test_playlist_stops_before_a_video_over_the_per_video_limit(tmp_path):
    plan = youtube.plan_playlist(_playlist([600, THREE_HOURS, 600]), tmp_path, None, 100.0, 3.0)
    assert [v.id for v in plan.todo] == ["v1"] and [v.id for v in plan.over_cap] == ["v2", "v3"]


def test_cli_link_over_the_per_video_limit_stops_before_the_download(fake_ydl):
    long_video = {**VIDEO_INFO, "duration": THREE_HOURS}
    fake_ydl.infos = {VIDEO_INFO["webpage_url"]: long_video}
    result = runner.invoke(cli.app, ["run", VIDEO_INFO["webpage_url"], "--dry-run"])
    assert result.exit_code == 1
    expected = "the estimate 3.87 USD (Claude 2.46 + speech recognition 1.41) is above --max-cost 3.00"
    assert expected in result.output
    result = runner.invoke(cli.app, ["run", VIDEO_INFO["webpage_url"], "--dry-run", "--max-cost", "4"])
    assert result.exit_code == 0, result.output
    assert not [c for c in fake_ydl.calls if c[0] == "download"]


def test_cli_local_file_over_the_per_video_limit_stops_before_any_work(sine, monkeypatch):
    from talk_autopsy import ingest

    started = []
    monkeypatch.setattr(ingest, "probe", lambda path: float(THREE_HOURS))
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: started.append(1))
    result = runner.invoke(cli.app, ["run", str(sine), "--steps", "srt"])
    assert result.exit_code == 1 and "above --max-cost 3.00 USD" in result.output and started == []
    result = runner.invoke(cli.app, ["run", str(sine), "--dry-run", "--max-cost", "4"])
    assert result.exit_code == 0, result.output


def test_the_web_gate_uses_the_same_limit():
    from talk_autopsy.web import jobs

    e = youtube.estimate(THREE_HOURS)
    preview = jobs.Preview(mode="url", source="x", title="t", duration_s=THREE_HOURS, cost=e, formats=["srt"],
                           time=jobs.NO_ESTIMATE)
    assert preview.needs_confirmation(config.COST_CONFIRMATION_USD) == youtube.over_video_limit(
        e, config.COST_CONFIRMATION_USD) is True
    assert preview.needs_confirmation(4.0) is False


# --- names cut at a word (v1.1 session 4); folders and files made before keep theirs ----------------

LONG_TITLE = "Create a Smart Contract in under 15 mins Step-by-Step Tutorial"


def _video(title=LONG_TITLE):
    return youtube.VideoInfo(id="v1", url="https://y/v1", title=title, upload_date="2024-07-05")


def test_long_names_are_cut_at_a_word():
    assert youtube.video_file_stem(_video()) == "Create a Smart Contract in under 15 mins Step-by-Step"
    assert youtube.safe_name("word " * 30, limit=12, words=True) == "word word"
    assert youtube.safe_name("x" * 200, words=True) == "x" * 80  # one long word: cut where it must be
    assert youtube.safe_name("short title", limit=60, words=True) == "short title"


def test_a_folder_and_a_download_made_before_keep_their_names(tmp_path):
    video = _video("A" * 30 + " " + "B" * 60)
    legacy_folder = youtube.safe_name(f"{video.upload_date} {video.title}")
    assert youtube.video_folder_name(video, tmp_path) != legacy_folder  # nothing there yet: the new name
    (tmp_path / legacy_folder).mkdir()
    assert youtube.video_folder_name(video, tmp_path) == legacy_folder
    folder = tmp_path / legacy_folder
    legacy_stem = youtube.safe_name(video.title, limit=60)
    assert youtube.video_file_stem(video, folder) != legacy_stem
    (folder / f"{legacy_stem}.mp4").write_bytes(b"downloaded before")
    assert youtube.video_file_stem(video, folder) == legacy_stem  # the download is reused, names kept
    course = "Course " * 20
    assert youtube.playlist_folder_name(course, tmp_path) == youtube.safe_name(course, words=True)
