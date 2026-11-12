"""The waiting screen's rows, built from events on the server (web/transcript.py)."""

import time

from talk_autopsy.web.transcript import MAX_WINDOW, TranscriptView, pieces


def recognized(view, texts, source="stt", replace=True, first_id=1):
    rows = [{"id": first_id + k, "start": 5.0 * k, "text": t} for k, t in enumerate(texts)]
    view.apply({"type": "transcript", "source": source, "replace": replace, "rows": rows})


def test_pieces_mark_removed_and_added_words():
    assert pieces("we use cloud code daily", "we use Claude Code daily") == [
        {"k": "same", "t": "we use"}, {"k": "del", "t": "cloud code"}, {"k": "ins", "t": "Claude Code"},
        {"k": "same", "t": "daily"}]
    assert pieces("unchanged", None) == [{"k": "same", "t": "unchanged"}]
    assert pieces("same", "same") == [{"k": "same", "t": "same"}]


def test_rows_follow_recognition_correction_and_translation():
    view = TranscriptView()
    recognized(view, ["Hello there,", "we use cloud code.", "A long sentence here."])
    window = view.window(0, 10)
    assert [len(r["segments"]) for r in window["rows"]] == [1, 1, 1]
    assert all(r["units"] == [] for r in window["rows"])
    view.apply({"type": "corrected", "position": 1, "total": 1, "failed": False, "ids": [1, 2, 3],
                "segments": [{"id": 1, "text": "Hello there,"}, {"id": 2, "text": "we use Claude Code."},
                             {"id": 3, "text": "A long sentence here."}],
                "edits": [{"id": 2, "before": "cloud code", "after": "Claude Code"}]})
    row = view.window(0, 10)["rows"][1]["segments"][0]
    assert row["changed"] and {"k": "ins", "t": "Claude Code."} in row["pieces"]
    # unit 1 spans segments 1-2; segment 3 is split into units 2 and 3
    view.apply({"type": "translated", "position": 1, "total": 1, "failed": False, "ids": [1, 2, 3],
                "units": [{"id": 1, "text": "Привет, мы используем Claude Code.", "source": "",
                           "source_ids": [1, 2]},
                          {"id": 2, "text": "Длинное", "source": "", "source_ids": [3]},
                          {"id": 3, "text": "предложение.", "source": "", "source_ids": [3]}]})
    window = view.window(0, 10)
    assert [[s["id"] for s in r["segments"]] for r in window["rows"]] == [[1, 2], [3]]
    assert [[u["text"] for u in r["units"]] for r in window["rows"]] == [
        ["Привет, мы используем Claude Code."], ["Длинное", "предложение."]]
    summary = window["summary"]
    assert summary["edits"] == 1 and summary["units"] == 3 and summary["corrected"] == 3
    assert window["rows"][1]["newest"] and summary["focus"] == 2


def test_the_second_pass_replaces_the_first():
    view = TranscriptView()
    recognized(view, ["first pass one", "first pass two"])
    recognized(view, ["second pass"], source="stt2", first_id=10)
    window = view.window(0, 10)
    assert [s["id"] for r in window["rows"] for s in r["segments"]] == [10]
    assert window["summary"]["source"] == "stt2" and window["summary"]["segments"] == 1


def test_failed_blocks_are_marked_and_keep_the_recognized_text():
    view = TranscriptView()
    recognized(view, ["one", "two"])
    view.apply({"type": "corrected", "position": 1, "total": 1, "failed": True, "ids": [1, 2]})
    view.apply({"type": "translated", "position": 1, "total": 1, "failed": True, "ids": [1],
                "source_ids": {"1": [1, 2]}})
    window = view.window(0, 10)
    row = window["rows"][0]
    assert all(s["failed"] and not s["changed"] for s in row["segments"])
    assert row["units"] == [{"id": 1, "text": None, "failed": True}]
    assert window["summary"]["failed_blocks"] == 2 and window["summary"]["units"] == 0


def test_windows_are_bounded_whole_rows():
    view = TranscriptView()
    recognized(view, [f"Sentence number {n}." for n in range(1, 1001)])
    window = view.window(10, 5)
    assert (window["start"], window["end"]) == (10, 15) and window["more_before"] and window["more_after"]
    assert len(view.window(0, 10_000)["rows"]) == MAX_WINDOW
    follow = view.window(count=100, follow=True)
    assert follow["end"] == 1000 and not follow["more_after"] and follow["rows"][-1]["newest"]
    # a unit across the window edge pulls in its whole row
    view.apply({"type": "translated", "position": 1, "total": 1, "failed": False, "ids": [1],
                "units": [{"id": 1, "text": "x", "source": "", "source_ids": [9, 10, 11]}]})
    window = view.window(10, 5)
    assert window["start"] == 8 and [s["id"] for s in window["rows"][0]["segments"]] == [9, 10, 11]


def test_an_hour_of_talk_stays_fast():
    view = TranscriptView()
    recognized(view, [f"Segment {n} with a few more words to make it look like speech." for n in range(1500)])
    for block in range(12):
        ids = list(range(1 + block * 125, 1 + (block + 1) * 125))
        view.apply({"type": "corrected", "position": block + 1, "total": 12, "failed": False, "ids": ids,
                    "segments": [{"id": i, "text": f"Segment {i - 1} with a few more words, like speech."}
                                 for i in ids], "edits": []})
        units = [{"id": i, "text": "перевод", "source": "", "source_ids": [i, i + 1] if i % 2 else [i]}
                 for i in ids]
        view.apply({"type": "translated", "position": block + 1, "total": 12, "failed": False, "ids": ids,
                    "units": units})
    # CPU time of this process, not wall time: other load on the machine made the wall-clock version
    # fail once in five full runs (1.25 s against about 0.1 s idle; v1.1 session 4)
    started = time.process_time()
    for start in range(0, 1500, 100):
        window = view.window(start, 120)
        assert len(window["rows"]) <= 120
    assert time.process_time() - started < 1.0  # 15 windows of an hour-long talk


def test_other_events_change_nothing():
    view = TranscriptView()
    assert not view.apply({"type": "step_started", "step": "fix"})
    assert view.version == 0 and view.window()["rows"] == []
