"""scripts/reading_speed.py: D1 numbers and the causes of fast cues (counts and timecodes only)."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reading_speed", ROOT / "scripts" / "reading_speed.py")
reading_speed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reading_speed)

RU = """1
00:00:00,000 --> 00:00:00,800
Короткий текст субтитра

2
00:00:01,000 --> 00:00:03,000
Тут достаточно времени.

3
00:00:03,000 --> 00:00:04,000
Очень длинная русская строка
на двух строках здесь
"""
EN = """1
00:00:00,000 --> 00:00:00,800
Short cue

2
00:00:01,000 --> 00:00:03,000
Plenty of time.

3
00:00:03,000 --> 00:00:04,000
A long English line too
"""


def test_srt_is_parsed_with_lines_joined():
    cues = reading_speed.parse_srt(RU)
    assert [(round(a, 3), round(b, 3)) for _, a, b in cues] == [(0, 0.8), (1, 3), (3, 4)]
    assert cues[2][0] == "Очень длинная русская строка на двух строках здесь"


def test_shares_count_strictly_above_each_threshold():
    s = reading_speed.shares(reading_speed.parse_srt(RU))
    assert s["cues"] == 3 and s["under_1s"] == 1
    assert s["above_17"] == 2 / 3 and s["above_20"] == 2 / 3 and s["above_25"] == 2 / 3


def test_causes_in_order_short_then_fast_speech_then_longer_russian():
    found = reading_speed.causes(reading_speed.parse_srt(RU), reading_speed.parse_srt(EN))
    assert found["short cue"] == ["00:00.0"]
    assert found["fast speech"] == ["00:03.0"]  # the English of that second needs 23 cps
    assert found["longer Russian"] == [] and found["other"] == []
