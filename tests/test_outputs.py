"""Output formats: --output parsing, ffmpeg commands (no real encoding), bilingual .ass, file naming."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from talk_autopsy import outputs
from talk_autopsy.glossary import Glossary, GlossaryEntry, GlossaryError, combine, domain_paths
from talk_autopsy.segments import Segment, Transcript, Word


@pytest.mark.parametrize(("value", "expected"), [
    ("srt,ass", ["srt", "ass"]),
    (" mp4-burned , SRT ", ["srt", "mp4-burned"]),
    ("all", list(outputs.FORMATS)),
    ("bilingual,all", list(outputs.FORMATS)),
])
def test_parse_outputs(value, expected):
    assert outputs.parse_outputs(value) == expected


@pytest.mark.parametrize("value", ["", "srt,vtt", "mp4"])
def test_parse_outputs_rejects(value):
    with pytest.raises(ValueError):
        outputs.parse_outputs(value)


def test_soft_command_copies_streams_and_tags_languages():
    cmd = outputs.soft_command("ffmpeg", Path("v.mp4"), Path("v.ru.srt"), Path("v.en.srt"),
                               Path("o.mp4"), "ru")
    joined = " ".join(cmd)
    assert "-c:v copy" in joined and "-c:a copy" in joined and "-c:s mov_text" in joined
    assert "language=rus" in joined and "language=eng" in joined
    assert cmd[cmd.index("-disposition:s:0") + 1] == "default"
    assert "libx264" not in joined


def test_burn_command_reencodes_with_the_ass_style():
    cmd = outputs.burn_command("ffmpeg", Path("C:/a b/v.mp4"), "burn-subtitles.ass", Path("o.mp4"))
    assert cmd[cmd.index("-vf") + 1] == "subtitles=burn-subtitles.ass"
    joined = " ".join(cmd)
    assert "-c:v libx264 -preset veryfast -crf 20" in joined and "-c:a copy" in joined


def _translated() -> Transcript:
    words = [Word(text=t, start=s, end=s + 0.4) for t, s in
             (("We", 0.0), ("test", 0.5), ("tokens.", 1.0), ("Then", 3.0), ("we", 3.5), ("fuzz.", 4.0))]
    return Transcript(segments=[
        Segment(id=1, start=0.0, end=1.4, text="Мы тестируем токены.", words=words[:3]),
        Segment(id=2, start=3.0, end=4.4, text="Потом фаззим.", words=words[3:]),
    ], language="ru", source="t")


def test_bilingual_ass_has_two_styles_and_english_on_top():
    text = outputs.bilingual_ass_text(_translated(), title="lesson")
    assert "Style: Default," in text and "Style: English,Arial,30," in text
    events = [line for line in text.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == 2
    assert events[0].endswith(r"{\rEnglish}We test tokens.\N{\rDefault}Мы тестируем токены.")
    assert r"{\rEnglish}Then we fuzz.\N{\rDefault}Потом фаззим." in events[1]


def test_english_words_follow_their_cue_within_a_segment():
    from talk_autopsy import subtitles
    t = _translated()
    cues = subtitles.build_cues(t)
    assert outputs.english_for_cues(t, cues) == ["We test tokens.", "Then we fuzz."]


def _work(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    for lang in ("ru", "en"):
        (work / f"vid.{lang}.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
        (work / f"vid.{lang}.ass").write_text("[Script Info]\n", encoding="utf-8")
    return work


def test_write_outputs_names_files_for_players(tmp_path):
    calls = []

    def fake_ffmpeg(command, **kwargs):
        calls.append((command, kwargs.get("cwd")))
        Path(command[-1] if Path(command[-1]).is_absolute() else Path(kwargs.get("cwd") or ".") / command[-1]
             ).write_bytes(b"mp4")
        return SimpleNamespace(returncode=0, stderr="")

    video = tmp_path / "Lesson 1.mp4"
    video.write_bytes(b"v")
    dest = tmp_path / "out"
    written = outputs.write_outputs(_work(tmp_path), "vid", "ru", dest, "Lesson 1", list(outputs.FORMATS),
                                    video=video, translated=_translated(), run_ffmpeg=fake_ffmpeg)
    names = sorted(w.path.name for w in written)
    assert names == sorted(["Lesson 1.srt", "Lesson 1.en.srt", "Lesson 1.ass", "Lesson 1.en.ass",
                            "Lesson 1.bilingual.ass", "Lesson 1.subs.mp4", "Lesson 1.ru.mp4"])
    assert all(w.path.is_file() for w in written)
    burn_command, burn_cwd = calls[-1]
    assert burn_cwd == dest and "subtitles=burn-subtitles.ass" in burn_command
    assert not (dest / outputs.BURN_FILTER_NAME).exists()  # the temporary copy is removed
    assert not list(dest.glob("*.partial.mp4"))


def test_failed_ffmpeg_leaves_no_partial_file(tmp_path):
    def failing(command, **kwargs):
        return SimpleNamespace(returncode=1, stderr="Invalid data")

    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    with pytest.raises(outputs.OutputError, match="Invalid data"):
        outputs.write_outputs(_work(tmp_path), "vid", "ru", tmp_path / "out", "v", ["mp4-burned"],
                              video=video,
                              run_ffmpeg=failing)
    assert not list((tmp_path / "out").glob("*.mp4"))


def test_mp4_outputs_need_the_video(tmp_path):
    with pytest.raises(outputs.OutputError, match="source video"):
        outputs.write_outputs(_work(tmp_path), "vid", "ru", tmp_path / "out", "v", ["mp4-soft"])


def test_burn_time_estimate_uses_the_measured_speed():
    assert outputs.burn_seconds(3600) == pytest.approx(3600 * outputs.BURN_SPEED)


# --- user glossaries: --glossary, --domain, local additions ---------------------------


def test_combine_earlier_glossary_wins():
    first = Glossary(entries=[GlossaryEntry(term="fuzzing", keep=True)])
    second = Glossary(entries=[GlossaryEntry(term="Fuzzing", keep=False, translations={"ru": "фаззинг"}),
                               GlossaryEntry(term="Foundry", keep=True)])
    combined = combine([first, second])
    assert [(e.term, e.keep) for e in combined.entries] == [("fuzzing", True), ("Foundry", True)]


def test_domain_paths(tmp_path):
    (tmp_path / "web3.md").write_text("", encoding="utf-8")
    local = tmp_path / "local"
    assert domain_paths("web3", tmp_path, local) == [tmp_path / "web3.md"]
    local.mkdir()
    (local / "web3.md").write_text("", encoding="utf-8")
    assert domain_paths("WEB3", tmp_path, local) == [tmp_path / "web3.md", local / "web3.md"]
    with pytest.raises(GlossaryError, match="known domains: web3"):
        domain_paths("ml", tmp_path, local)
    with pytest.raises(GlossaryError):
        domain_paths("../x", tmp_path, local)


def test_web3_glossary_parses_and_fits_the_key_term_limits():
    from talk_autopsy import config
    from talk_autopsy.glossary import load_glossary
    from talk_autopsy.stt import assemblyai
    glossary = load_glossary(config.GLOSSARIES_DIR / "web3.md")
    assert len(glossary.entries) >= 100
    assert len(assemblyai.keyterms(glossary)) == len(glossary.entries) <= assemblyai.KEYTERM_MAX_COUNT
    assert glossary.find("msg.sender").keep and glossary.find("smart contract").translations["ru"]


def test_domain_glossary_wins_over_the_auto_glossary_and_comes_first_in_key_terms():
    from talk_autopsy import glossary_auto
    from talk_autopsy.stt import assemblyai
    domain = Glossary(entries=[GlossaryEntry(term="fuzzing", keep=True),
                               GlossaryEntry(term="Foundry", keep=True)])
    auto = Glossary(entries=[GlossaryEntry(term="Anvil", keep=True),
                             GlossaryEntry(term="Fuzzing", keep=False, translations={"ru": "фаззинг"})])
    merged = glossary_auto.merge(auto, domain)
    assert [e.term for e in merged.entries] == ["fuzzing", "Foundry", "Anvil"]
    assert merged.find("fuzzing").keep is True
    assert assemblyai.keyterms(merged)[:2] == ["fuzzing", "Foundry"]
