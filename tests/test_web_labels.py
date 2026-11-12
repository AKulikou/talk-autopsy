"""What a result file is for, from its name (web/labels.py): the Done screen's file list."""

import pytest

from talk_autopsy.web.labels import file_kind, stem_of

STEM = "Create a Smart Contract"


@pytest.mark.parametrize("name, kind", [
    (f"{STEM}.srt", "subs_target"), (f"{STEM}.en.srt", "subs_en"), (f"{STEM}.ass", "styled_target"),
    (f"{STEM}.en.ass", "styled_en"), (f"{STEM}.bilingual.ass", "bilingual"), (f"{STEM}.ru.mp4", "tv"),
    (f"{STEM}.subs.mp4", "video_tracks"), (f"{STEM}.mp4", "original"),
    (f"{STEM}.youtube.en.vtt", "youtube_captions"),
    ("lecture.mp3", "audio"), ("notes.txt", "other"),
])
def test_every_output_name_has_a_kind(name, kind):
    assert file_kind(name, STEM, "ru") == kind


def test_the_two_srt_files_are_told_apart_and_the_stem_is_the_target_srt():
    files = [f"{STEM}.en.srt", f"{STEM}.srt", f"{STEM}.youtube.en.srt"]
    assert stem_of(files) == STEM
    assert {file_kind(f, STEM, "ru") for f in files} == {"subs_en", "subs_target", "youtube_captions"}


def test_every_kind_and_target_language_has_words_in_every_language():
    import json

    from talk_autopsy.web import settings
    kinds = ("subs_target", "subs_en", "styled_target", "styled_en", "bilingual", "tv", "video_tracks",
             "original",
             "youtube_captions", "audio")
    for lang in ("en", "ru", "lt"):
        strings = json.loads((settings.STRINGS_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        assert {f"file_{k}" for k in kinds} <= set(strings), lang
        assert {f"langname_{c}" for c in [*settings.target_codes(), "en"]} <= set(strings), lang
