"""scripts/check_names.py: the local name list and the committed patterns, both across line breaks."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_names", ROOT / "scripts" / "check_names.py")
check_names = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_names)

PATTERNS = check_names.read_patterns(ROOT / check_names.PATTERNS_FILE)


def _hits(text: str, names=None, patterns=None, tmp_path: Path | None = None) -> list:
    (tmp_path / "doc.md").write_text(text, encoding="utf-8")
    return check_names.scan(["doc.md"], names, patterns, root=tmp_path)


def test_a_name_split_across_a_line_break_is_found(tmp_path):
    text = "First line.\nThe talk by Jane\n  Example on agents.\n"
    hits = _hits(text, names=["Jane Example"], tmp_path=tmp_path)
    assert [(h.line, h.source) for h in hits] == [(2, "list entry 1")]


def test_names_match_case_insensitively_and_report_the_entry_number_only(tmp_path):
    hits = _hits("one\ntwo JANE EXAMPLE three\n", names=["Other Person", "jane example"], tmp_path=tmp_path)
    assert [(h.file, h.line, h.source) for h in hits] == [("doc.md", 2, "list entry 2")]


def test_names_match_whole_words_only(tmp_path):
    hits = _hits("pyjaneexample and Jane's talk\n", names=["Jane"], tmp_path=tmp_path)
    assert [(h.line, h.source) for h in hits] == [(1, "list entry 1")]  # "Jane's", not "pyjane..."


@pytest.mark.parametrize("text", [
    # [removed]
    "the speaker's Italian-accented English",
    "an Eastern European English accent",
    "accent: Brazilian",
    # [removed]
    "an OpenAI researcher said",
    "researcher at DeepMind",
    "run data/test-set/aB3dE5gH7jK9mN1p.mp4 --name x",
])
def test_the_committed_patterns_find_identifying_descriptors(tmp_path, text):
    assert _hits(text, patterns=PATTERNS, tmp_path=tmp_path)


@pytest.mark.parametrize("text", [
    "T2 (a non-native speaker), T1 (a native speaker)",
    "WER <= 15% on the accented fragment; accent-distorted words",
    "Russian subtitles; the Russian Windows UI",
    "run data/test-set/T2.source.mp4 --lang ru; Lesson 1.mp4; T6.wav",
    "Google Translate; Anthropic's model; a job title by meaning",
])
def test_the_committed_patterns_leave_generic_wording_alone(tmp_path, text):
    assert _hits(text, patterns=PATTERNS, tmp_path=tmp_path) == []


def test_pattern_hits_report_the_line_of_the_match_start(tmp_path):
    hits = _hits("ok\nok\nstrong Polish\naccent here\n", patterns=PATTERNS, tmp_path=tmp_path)
    assert [(h.line, h.source) for h in hits] == [(3, "pattern 1")]


def test_the_check_files_themselves_are_exempt(tmp_path):
    (tmp_path / "scripts").mkdir()
    # [removed]
    # [removed]


def test_every_tracked_file_passes_the_committed_patterns():
    """What CI runs (`--patterns-only`); the local name list is checked by scripts/check.ps1."""
    hits = check_names.scan(check_names.tracked_files(), None, PATTERNS)
    assert hits == [], [f"{h.file}:{h.line}: {h.source}" for h in hits]


def test_a_missing_local_list_is_an_error_unless_patterns_only(tmp_path, capsys):
    assert check_names.main(["--names", str(tmp_path / "absent.txt")]) == 2
    assert "--patterns-only" in capsys.readouterr().out


def test_names_are_allowed_only_in_the_sources_section_of_the_test_set_readme(tmp_path):
    readme = (
        "# Test set\n\nJane Example in the intro.\n\n## Sources\n\n| T9 | Jane Example |\n\n"
        "### Notes\n\nJane Example again.\n\n## What each fragment tests\n\nJane Example here too.\n"
    )
    (tmp_path / "data" / "test-set").mkdir(parents=True)
    (tmp_path / "data" / "test-set" / "README.md").write_text(readme, encoding="utf-8")
    (tmp_path / "other.md").write_text("## Sources\n\nJane Example\n", encoding="utf-8")
    hits = check_names.scan(["data/test-set/README.md", "other.md"], ["Jane Example"], None, root=tmp_path)
    # Line 3 (before the section) and line 15 (after it) are hits; the section with its subsection is not;
    # the same heading in another file allows nothing.
    readme = "data/test-set/README.md"
    assert [(h.file, h.line) for h in hits] == [(readme, 3), (readme, 15), ("other.md", 3)]


def test_the_sources_section_is_still_scanned_by_the_committed_patterns(tmp_path):
    (tmp_path / "data" / "test-set").mkdir(parents=True)
    (tmp_path / "data" / "test-set" / "README.md").write_text(
        "# Test set\n\n## Sources\n\nan OpenAI researcher\n", encoding="utf-8")
    hits = check_names.scan(["data/test-set/README.md"], None, PATTERNS, root=tmp_path)
    assert [(h.line, h.source) for h in hits] == [(5, "pattern 5")]
