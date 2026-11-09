"""Audio extraction on a generated 2-second sine wave; needs ffmpeg on PATH (CI installs it)."""

import wave

import pytest

from talk_autopsy import ingest


def wav_info(path):
    with wave.open(str(path)) as w:
        return w.getnchannels(), w.getframerate(), w.getsampwidth(), w.getnframes() / w.getframerate()


def test_probe_reads_the_duration(sine):
    assert ingest.probe(sine) == pytest.approx(2.0, abs=0.05)


def test_whole_file_becomes_16k_mono_16bit(sine, tmp_path):
    out = ingest.extract_audio(sine, tmp_path / "out" / "audio.wav")
    channels, rate, width, seconds = wav_info(out)
    assert (channels, rate, width) == (1, 16000, 2)
    assert seconds == pytest.approx(2.0, abs=0.05)
    assert not list(out.parent.glob("*.partial.wav"))


def test_cut_by_timecodes(sine, tmp_path):
    out = ingest.extract_audio(sine, tmp_path / "cut.wav", start=0.5, end=1.5)
    assert wav_info(out)[3] == pytest.approx(1.0, abs=0.05)


def test_cut_from_start_only(sine, tmp_path):
    out = ingest.extract_audio(sine, tmp_path / "tail.wav", start=1.5)
    assert wav_info(out)[3] == pytest.approx(0.5, abs=0.05)


@pytest.mark.parametrize("start, end, message", [
    (1.5, 1.0, "must be after start"),
    (3.0, None, "past the end"),
    (0.0, 10.0, "past the end"),
    (-1.0, 1.0, "negative"),
])
def test_a_cut_outside_the_file_is_an_error_not_a_shorter_file(sine, tmp_path, start, end, message):
    out = tmp_path / "bad.wav"
    with pytest.raises(ValueError, match=message):
        ingest.extract_audio(sine, out, start=start, end=end)
    assert not out.exists()


def test_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError):
        ingest.extract_audio(tmp_path / "nope.mp4", tmp_path / "out.wav")


def test_missing_ffmpeg_names_what_to_install(monkeypatch, sine):
    monkeypatch.setattr(ingest.shutil, "which", lambda name: None)
    with pytest.raises(ingest.ToolNotFoundError, match="Install ffmpeg"):
        ingest.probe(sine)


@pytest.mark.parametrize("value, seconds", [
    ("0", 0.0), ("90", 90.0), ("12.5", 12.5), ("10:00", 600.0), ("1:02.5", 62.5),
    ("00:00", 0.0), ("1:00:00", 3600.0), ("01:27:52", 5272.0),
])
def test_timecodes(value, seconds):
    assert ingest.parse_timecode(value) == pytest.approx(seconds)


@pytest.mark.parametrize("value", ["", "abc", "1:2:3:4", "10:60", "1:60:00", "-5", "5s"])
def test_bad_timecodes(value):
    with pytest.raises(ValueError):
        ingest.parse_timecode(value)


def test_download_is_a_stage_1_stub(tmp_path):
    with pytest.raises(NotImplementedError, match="stage 1"):
        ingest.download("https://example.com/video", tmp_path)
