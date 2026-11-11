"""The eval command and the benchmark row: files in a temporary test-set directory."""

import csv
import re

import pytest
from typer.testing import CliRunner

from talk_autopsy import cli, config, evaluation
from talk_autopsy.segments import Segment, Transcript, save_transcript

runner = CliRunner()

ANSI = re.compile(chr(27) + r"\[[0-9;]*m")  # ESC [ ... m


def plain(output: str) -> str:
    """CLI output without ANSI colours: on GitHub Actions Typer colours the usage errors."""
    return ANSI.sub("", output)

HEADER = ",".join(evaluation.BENCHMARK_COLUMNS)
GLOSSARY = """<!-- draft: not yet reviewed -->
| term | keep | ru | notes |
|---|---|---|---|
| Claude Code | yes | | product |
| npm | yes | | |
| Docker | yes | | |
"""


@pytest.fixture
def test_set(tmp_path, monkeypatch):
    """A test-set directory with T9's reference and glossary, and an empty benchmark table."""
    directory = tmp_path / "test-set"
    directory.mkdir()
    (directory / "T9.ref.txt").write_text(
        "Open Claude Code, then npm install.\n\nClaude Code runs it.\n", encoding="utf-8")
    (directory / "T9.glossary.md").write_text(GLOSSARY, encoding="utf-8")
    csv_path = tmp_path / "benchmark.csv"
    csv_path.write_bytes((HEADER + "\n").encode())  # LF, as the repository stores it
    monkeypatch.setattr(config, "TEST_SET_DIR", directory)
    monkeypatch.setattr(config, "BENCHMARK_CSV", csv_path)
    return directory


def _transcript(*texts, **meta):
    segments = [Segment(id=i, start=i * 2.0, end=i * 2.0 + 1.5, text=t) for i, t in enumerate(texts, 1)]
    return Transcript(segments=segments, language="en", source="test", meta=meta)


def test_eval_prints_wer_and_terms(test_set, tmp_path):
    hyp = save_transcript(_transcript(" Open cloud code, then npm install.", " Claude Code runs it."),
                          tmp_path / "raw.json")
    result = runner.invoke(cli.app, ["eval", "T9", "--hyp", str(hyp)])
    assert result.exit_code == 0, result.output
    # 10 reference words, one substitution (claude -> cloud)
    assert "10.00%" in result.output and "S 1, D 0, I 0" in result.output
    assert "1/2" in result.output  # Claude Code: found once of twice
    assert "glossary is a draft" in result.output
    assert "Docker" in result.output and "excluded" in result.output


def test_eval_reads_plain_text_and_skips_terms_without_a_glossary(test_set, tmp_path):
    (test_set / "T9.glossary.md").unlink()
    hyp = tmp_path / "hyp.txt"
    hyp.write_text("open claude code then npm install claude code runs it", encoding="utf-8")
    result = runner.invoke(cli.app, ["eval", "T9", "--hyp", str(hyp)])
    assert result.exit_code == 0, result.output
    assert "0.00%" in result.output and "skipped" in result.output


def test_eval_with_raw_reports_false_edits(test_set, tmp_path):
    raw = tmp_path / "raw.txt"
    raw.write_text("open cloud code then npm install claude code runs it", encoding="utf-8")
    fixed = tmp_path / "fixed.txt"
    fixed.write_text("open claude code then pip install claude code runs it", encoding="utf-8")
    result = runner.invoke(cli.app, ["eval", "T9", "--hyp", str(fixed), "--raw", str(raw)])
    assert result.exit_code == 0, result.output
    # two word edits: cloud -> claude moves closer to the reference, npm -> pip away from it
    assert "50.00%" in result.output
    assert "1 of 2 word edits move the text away from the reference; 1 move it closer" in result.output


def test_record_appends_one_row_with_the_existing_header(test_set, tmp_path):
    hyp = save_transcript(_transcript("Open cloud code, then npm install.", "Claude Code runs it.",
                                      wall_seconds=300.0, cost_usd=0.0), tmp_path / "raw.json")
    args = ["eval", "T9", "--hyp", str(hyp), "--record", "--engine", "fw-test", "--stage", "stt"]
    for _ in range(2):
        assert runner.invoke(cli.app, args).exit_code == 0
    with config.BENCHMARK_CSV.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    row = rows[0]
    assert list(row) == HEADER.split(",")
    assert row["fragment"] == "T9" and row["stage"] == "stt" and row["engine_or_prompt"] == "fw-test"
    assert row["wer"] == "0.1000"
    assert row["term_accuracy_v2"] == "0.6667"  # Claude Code 1/2 + npm 1/1, in place
    assert row["term_accuracy_v1"] == "0.6667"
    assert row["term_accuracy_v2_macro"] == "0.7500"  # (1/2 + 1/1) / 2
    assert row["term_accuracy_v2_without_top"] == "1.0000"  # without Claude Code: npm 1/1
    assert row["term_precision"] == "1.0000" and row["term_false_positives"] == "0"
    assert row["false_edit_rate_v2"] == "" and row["false_edit_rate_v1"] == ""
    assert row["status"] == "current"
    assert row["minutes"] == "5.0" and row["cost_usd"] == "0.0000"
    assert row["notes"] == "draft glossary"
    assert config.BENCHMARK_CSV.read_bytes().count(b"\r") == 0


def test_record_needs_engine_and_stage(test_set, tmp_path):
    hyp = tmp_path / "hyp.txt"
    hyp.write_text("x", encoding="utf-8")
    result = runner.invoke(cli.app, ["eval", "T9", "--hyp", str(hyp), "--record", "--engine", "e"])
    assert result.exit_code == 2 and "--stage" in plain(result.output)


def test_missing_reference_points_to_ref_from_draft(test_set, tmp_path):
    hyp = tmp_path / "hyp.txt"
    hyp.write_text("x", encoding="utf-8")
    result = runner.invoke(cli.app, ["eval", "T8", "--hyp", str(hyp)])
    assert result.exit_code == 1 and "resolve T" in result.output


def test_bad_fragment_id_is_rejected(test_set, tmp_path):
    result = runner.invoke(cli.app, ["eval", "../etc", "--hyp", str(tmp_path / "x.txt")])
    assert result.exit_code == 2


def test_header_mismatch_is_an_error(tmp_path):
    path = tmp_path / "b.csv"
    path.write_text("date,fragment\n", encoding="utf-8")
    with pytest.raises(ValueError, match="header"):
        evaluation.append_benchmark_row(path, {"date": "x", "fragment": "y", "wer": "z"})


def test_row_without_a_trailing_newline_is_appended_on_its_own_line(tmp_path):
    path = tmp_path / "b.csv"
    path.write_bytes(b"a,b\n1,2")
    evaluation.append_benchmark_row(path, {"a": "3", "b": "4"})
    assert path.read_bytes() == b"a,b\n1,2\n3,4\n"


def test_append_keeps_a_crlf_file_crlf(tmp_path):
    path = tmp_path / "b.csv"
    path.write_bytes(b"a,b\r\n")
    evaluation.append_benchmark_row(path, {"a": "3", "b": "4"})
    assert path.read_bytes() == b"a,b\r\n3,4\r\n"


def test_unmeasured_numbers_stay_empty():
    report = evaluation.evaluate("a b c", "a b c")
    row = evaluation.benchmark_row(report, fragment="T1", stage="stt", engine="e", meta={})
    assert row["minutes"] == "" and row["cost_usd"] == "" and row["term_accuracy_v2"] == ""
    assert row["term_accuracy_v1"] == "" and row["false_edit_rate_v2"] == ""
