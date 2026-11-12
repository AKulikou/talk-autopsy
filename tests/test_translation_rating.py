"""The baselines' translation rating: second file, anchors, Gemini rater (fake caller), scorer, docs."""

import importlib.util
import json
from types import SimpleNamespace

import pytest

from talk_autopsy import config
from talk_autopsy import translation_check as tc
from talk_autopsy import translation_rating as tr

UNITS = [24, 32, 35, 52, 58, 62, 74, 76, 97, 108, 124, 135]


def first_file():
    """A first rating like the real one: 12 units, units 32 and 52 shared, 22 items."""
    items = [tc.Item(u, f"English sentence of unit {u}.", f"Перевод {u}.") for u in UNITS]
    baseline = {u: (f"Перевод {u}." if u in (32, 52) else f"Другой перевод {u}.") for u in UNITS}
    text, key = tc.rating_file(items, baseline)
    return text, key


def filled(text: str, scores: dict[int, int]) -> str:
    out, current = [], None
    for line in text.splitlines():
        if line.startswith("## "):
            current = int(line[3:])
        if line.startswith("Score (1-5):"):
            line = f"Score (1-5): {scores[current]}"
        out.append(line)
    return "\n".join(out)


def google():
    return {u: f"Гугл {u}." for u in UNITS}


def second_file():
    text1, key1 = first_file()
    first = tr.with_units(tr.parse_items(text1), key1)
    text2, key2 = tr.rating_file_2(first, key1, google())
    return text1, key1, first, text2, key2


def test_parse_items_reads_what_was_shown_and_needs_the_key_for_units():
    text, key = first_file()
    shown = tr.parse_items(filled(text, {int(n): 4 for n in key["items"]}))
    assert len(shown) == 22
    with_units = tr.with_units(shown, key)
    assert all(s.english == f"English sentence of unit {s.unit}." for s in with_units.values())
    with pytest.raises(tr.RatingError):
        tr.with_units(dict(list(shown.items())[:5]), key)


def test_anchors_are_two_pipeline_then_two_gemini_items_of_other_units_never_shared():
    _, key1 = first_file()
    anchors = tr.choose_anchors(key1)
    systems = [key1["items"][str(n)]["system"] for n in anchors]
    assert systems == [tc.PIPELINE, tc.PIPELINE, tc.BASELINE, tc.BASELINE]
    assert len({key1["items"][str(n)]["unit"] for n in anchors}) == 4
    assert anchors == tr.choose_anchors(key1)  # seeded


def test_second_file_has_16_items_anchors_shown_as_before_and_no_adjacent_unit():
    _, key1, first, text2, key2 = second_file()
    assert len(key2["items"]) == 16 and "двух" not in text2 and "разных систем" in text2
    shown = tr.with_units(tr.parse_items(text2), key2)
    for number, meta in key2["items"].items():
        if "anchor_of" in meta:
            assert shown[int(number)] == first[meta["anchor_of"]]
        else:
            assert meta["system"] == tr.GOOGLE
            assert shown[int(number)].translation == f"Гугл {meta['unit']}."
            assert shown[int(number)].english == f"English sentence of unit {meta['unit']}."
    units = [key2["items"][str(n)]["unit"] for n in range(1, 17)]
    assert all(a != b for a, b in zip(units, units[1:], strict=False))
    assert "Score (1-5):\n" in text2 and "Score (1-5): " not in text2  # no score shown
    with pytest.raises(tr.RatingError):
        tr.rating_file_2(first, key1, {u: "x" for u in UNITS[:-1]})


class FakeRater:
    def __init__(self, score=4, cost=0.002):
        self.calls, self.spent_usd, self.score, self.cost = [], 0.0, score, cost

    def generate(self, contents, schema):
        self.calls.append(contents[0])
        self.spent_usd += self.cost
        return SimpleNamespace(text=json.dumps({"reason": "fine", "score": self.score}))


def rater_record(items):
    return {"version": tr.RATER_VERSION, "model": "fake", "order": tr.rater_order(list(items)),
            "cost_usd": 0.0, "items": []}


def test_rater_rates_every_item_once_in_a_seeded_order_and_resumes():
    _, _, first, text2, key2 = second_file()
    items = tr.rater_items(first, tr.with_units(tr.parse_items(text2), key2))
    assert len(items) == 38
    record, saved = rater_record(items), []
    caller = FakeRater()
    tr.rate(items, caller, record, saved.append)
    assert len(record["items"]) == 38 and len(saved) == 38
    assert [r["item"] for r in record["items"]] == record["order"] != sorted(record["order"])
    assert "EN: " in caller.calls[0] and "от 1 до 5" in caller.calls[0]
    again = FakeRater()
    tr.rate(items, again, record, saved.append)
    assert again.calls == []  # resumed: nothing left


def test_rater_stops_before_crossing_the_budget():
    _, _, first, text2, key2 = second_file()
    items = tr.rater_items(first, tr.with_units(tr.parse_items(text2), key2))
    record = rater_record(items)
    tr.rate(items, FakeRater(cost=0.05), record, lambda r: None, budget_usd=0.30)
    assert len(record["items"]) == 6 and record["stopped"] and record["cost_usd"] == pytest.approx(0.30)


def test_rater_rejects_a_malformed_answer():
    _, _, first, text2, key2 = second_file()
    items = tr.rater_items(first, tr.with_units(tr.parse_items(text2), key2))
    bad = FakeRater(score=7)
    with pytest.raises(tr.RatingError, match="not 1-5"):
        tr.rate(items, bad, rater_record(items), lambda r: None)


def scored():
    _, key1, _, _, key2 = second_file()
    units1 = {}
    for number, meta in key1["items"].items():
        units1.setdefault(meta["unit"], {})[meta["system"]] = int(number)
    # pipeline 5 everywhere, Gemini 4, shared 5; Google Translate 3
    scores1 = {int(n): (4 if m["system"] == tc.BASELINE else 5) for n, m in key1["items"].items()}
    scores2 = {}
    for number, meta in key2["items"].items():
        scores2[int(number)] = 3 if meta["system"] == tr.GOOGLE else scores1[meta["anchor_of"]] - 1
    return scores1, key1, scores2, key2


def test_score_baselines_means_differences_drift_and_c1():
    scores1, key1, scores2, key2 = scored()
    result = tr.score_baselines(scores1, key1, scores2, key2)
    assert result.units == 12
    assert result.means[tr.PIPELINE] == 5.0 and result.means[tr.GOOGLE] == 3.0
    assert result.means[tr.GEMINI] == pytest.approx((10 * 4 + 2 * 5) / 12)
    assert result.differences[tr.GOOGLE][0] == 2.0 and result.differences[tr.GOOGLE][1] == (2.0, 2.0)
    assert result.drift_mean_abs == 1.0 and result.drift_signed == -1.0 and result.drift_flag
    assert result.c1_google_met
    assert "drift ≥ 1 point" in tr.readme_cells(result)[tr.GOOGLE]
    text = tr.result_markdown(result, "2026-11-12")
    assert "**3.00**" in text and "**met**" in text and "Gemini as a second rater" not in text


def test_pipeline_minus_gemini_reproduces_the_first_check():
    scores1, key1, scores2, key2 = scored()
    scores1 = {n: (n % 5) + 1 for n in scores1}
    first = tc.score(scores1, key1)
    result = tr.score_baselines(scores1, key1, scores2, key2)
    assert result.differences[tr.GEMINI] == (pytest.approx(first.difference), first.interval)


def test_score_baselines_needs_every_score():
    scores1, key1, scores2, key2 = scored()
    del scores2[1]
    with pytest.raises(tr.RatingError):
        tr.score_baselines(scores1, key1, scores2, key2)


def test_agreement_per_system_counts_shared_items_for_both():
    scores1, key1, scores2, key2 = scored()
    given = [{"item": f"1-{n}", "score": s} for n, s in scores1.items()]
    given += [{"item": f"2-{n}", "score": min(5, s + 1)} for n, s in scores2.items()]
    result = tr.score_baselines(scores1, key1, scores2, key2, {"items": given})
    assert result.rater_complete
    assert result.agreement["all"].n == 38
    assert result.agreement[tr.GOOGLE].n == 12 and result.agreement[tr.GOOGLE].bias == 1.0
    assert result.agreement[tr.PIPELINE].n == 10 + 2 + 2  # pipeline-only, shared, anchors
    assert result.agreement[tr.GEMINI].n == 10 + 2 + 2
    assert "Self-preference" in tr.result_markdown(result, "2026-11-12")
    partial = tr.score_baselines(scores1, key1, scores2, key2, {"items": given[:5]})
    assert not partial.rater_complete and "incomplete" in tr.result_markdown(partial, "2026-11-12")


def test_spearman_with_ties_and_constants():
    assert tr.spearman([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert tr.spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert tr.spearman([1, 1, 2], [1, 2, 2]) == pytest.approx(0.5)
    assert tr.spearman([4, 4, 4], [1, 2, 3]) is None


def test_docs_and_readme_are_updated_between_markers_only():
    scores1, key1, scores2, key2 = scored()
    result = tr.score_baselines(scores1, key1, scores2, key2)
    docs = f"before\n{tr.START}\nold\n{tr.END}\nafter\n"
    assert tr.replace_between(docs, tr.START, tr.END, "new") == f"before\n{tr.START}\nnew\n{tr.END}\nafter\n"
    with pytest.raises(tr.RatingError):
        tr.replace_between("no markers", tr.START, tr.END, "x")
    readme = (f"x\n{tr.TABLE_START}\n| | a | rating |\n|---|---|---|\n| Talk Autopsy (v3) | 1 | *pending* |\n"
              "| raw Whisper | 2 | — |\n| Whisper + Google Translate | 3 | *pending* |\n"
              f"| Gemini translation (plain) | 4 | *pending* |\n{tr.TABLE_END}\ny\n")
    out = tr.update_readme_table(readme, result)
    assert "| Talk Autopsy (v3) | 1 | **5.00** |" in out and "| raw Whisper | 2 | — |" in out
    row = "| Whisper + Google Translate | 3 | 3.00 (pipeline +2.00, +2.00 … +2.00); rated on a different day"
    assert row in out
    assert out.startswith("x\n") and out.endswith(f"{tr.TABLE_END}\ny\n")


def test_readme_update_keeps_empty_cells():
    """Regression: splitting on ' | ' merged an empty cell into the last one and dropped a column."""
    scores1, key1, scores2, key2 = scored()
    result = tr.score_baselines(scores1, key1, scores2, key2)
    readme = (f"{tr.TABLE_START}\n| | a | b | c | rating |\n|---|---|---|---|---|\n"
              "| Talk Autopsy (v3) | 1 | | 3 | *pending* |\n"
              "| Whisper + Google Translate | | | | *pending* |\n"
              f"| Gemini translation (plain) | as above | | as above | | *pending* |\n{tr.TABLE_END}\n")
    out = tr.update_readme_table(readme, result).split("\n")
    rows = [line for line in out if line.startswith(("| Talk", "| Whisper", "| Gemini"))]
    assert rows[0] == "| Talk Autopsy (v3) | 1 | | 3 | **5.00** |"
    assert rows[1].startswith("| Whisper + Google Translate | | | | 3.00 (pipeline")
    assert rows[2].startswith("| Gemini translation (plain) | as above | | as above | | ")
    assert [row.count("|") for row in rows] == [6, 6, 7]


def test_real_docs_carry_the_markers():
    for name, (start, end) in (("docs/experiments.md", (tr.START, tr.END)),
                               ("README.md", (tr.TABLE_START, tr.TABLE_END))):
        text = (config.PROJECT_ROOT / name).read_text(encoding="utf-8")
        assert text.count(start) == 1 and text.count(end) == 1, name


def _script():
    path = config.PROJECT_ROOT / "scripts" / "translation_check.py"
    spec = importlib.util.spec_from_file_location("translation_check_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_rating_2_and_score_baselines_end_to_end(tmp_path, monkeypatch, capsys):
    script = _script()
    text1, key1 = first_file()
    scores1, _, _, _ = scored()
    (tmp_path / "r1.md").write_text(filled(text1, scores1), encoding="utf-8")
    tc.write_json(tmp_path / "k1.json", key1)
    tc.write_json(tmp_path / "g.json",
                  {"items": [{"unit": u, "translation": t} for u, t in google().items()]})
    common = ["--rating", str(tmp_path / "r1.md"), "--key", str(tmp_path / "k1.json"),
              "--rating-2", str(tmp_path / "r2.md"), "--key-2", str(tmp_path / "k2.json"),
              "--google", str(tmp_path / "g.json"), "--rater-out", str(tmp_path / "rater.json")]
    assert script.main(["rating-2", *common]) == 0
    key2 = json.loads((tmp_path / "k2.json").read_text(encoding="utf-8"))
    scores2 = {int(n): 3 for n in key2["items"]}
    (tmp_path / "r2.md").write_text(filled((tmp_path / "r2.md").read_text(encoding="utf-8"), scores2),
                                    encoding="utf-8")
    assert script.main(["rating-2", *common]) == 1  # a filled file is never overwritten
    docs, readme = tmp_path / "docs.md", tmp_path / "README.md"
    docs.write_text(f"{tr.START}\nold\n{tr.END}\n", encoding="utf-8")
    readme.write_text(f"{tr.TABLE_START}\n| Talk Autopsy | x |\n| Whisper + Google Translate | x |\n"
                      f"| Gemini translation | x |\n{tr.TABLE_END}\n", encoding="utf-8")
    monkeypatch.setattr(script, "DOCS", docs)
    monkeypatch.setattr(script, "README", readme)
    assert script.main(["score-baselines", *common]) == 0
    assert "Whisper + Google Translate | **3.00**" in docs.read_text(encoding="utf-8")
    assert "| Whisper + Google Translate | 3.00 (pipeline" in readme.read_text(encoding="utf-8")
    assert "Drift" in capsys.readouterr().out
