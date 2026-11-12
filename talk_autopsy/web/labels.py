"""
What a result file is for, from its name (v1.1 session 4: the owner saw both
.srt files labelled "subtitles for VLC"). The page turns the kind into a label
in the interface language, with the target language's name where it matters.

    <stem>.srt            subs_target        <stem>.en.srt        subs_en
    <stem>.ass            styled_target      <stem>.en.ass        styled_en
    <stem>.bilingual.ass  bilingual          <stem>.<lang>.mp4    tv (burned in)
    <stem>.subs.mp4       video_tracks       <stem>.mp4           original (the download)
    <stem>.youtube.*      youtube_captions   audio files          audio
"""

from pathlib import Path

AUDIO_SUFFIXES = {".m4a", ".mp3", ".wav", ".ogg", ".opus", ".aac", ".flac"}


def file_kind(name: str, stem: str | None, lang: str = "") -> str:
    """The kind of a result file (see the module docstring); "other" when the name says nothing."""
    if not stem or not name.startswith(stem):
        return "audio" if Path(name).suffix.lower() in AUDIO_SUFFIXES else "other"
    rest = name[len(stem):]
    known = {".srt": "subs_target", ".en.srt": "subs_en", ".ass": "styled_target", ".en.ass": "styled_en",
             ".bilingual.ass": "bilingual", ".subs.mp4": "video_tracks", ".mp4": "original"}
    if rest in known:
        return known[rest]
    if rest.startswith(".youtube."):
        return "youtube_captions"
    if rest.endswith(".mp4") and rest.count(".") == 2 and (not lang or rest == f".{lang}.mp4"):
        return "tv"
    if Path(name).suffix.lower() in AUDIO_SUFFIXES:
        return "audio"
    return "other"


def stem_of(files: list[str]) -> str | None:
    """The result's file stem: the name of its target-language .srt."""
    targets = sorted(f for f in files
                     if f.endswith(".srt") and not f.endswith(".en.srt") and ".youtube." not in f)
    return targets[0][:-4] if targets else None
