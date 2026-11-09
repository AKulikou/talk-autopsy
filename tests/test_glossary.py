"""The glossary table format: parsing, errors with line numbers, round trip."""

import pytest

from talk_autopsy.glossary import GlossaryError, load_glossary, parse_glossary, to_markdown

SAMPLE = r"""# T1 glossary

Terms for the first fragment.

| Term | Keep | ru | de | Notes |
|------|:----:|----|----|-------|
| Claude Code | yes | | | product name |
| subagent | no | субагент | Subagent | |
| pipe \| filter | no | конвейер | | literal bar in a term |

Text after the table is ignored.
"""


def test_sample_table_is_parsed():
    glossary = parse_glossary(SAMPLE)
    assert [e.term for e in glossary.entries] == ["Claude Code", "subagent", "pipe | filter"]
    product, subagent, pipe = glossary.entries
    assert product.keep and product.translations == {} and product.notes == "product name"
    assert not subagent.keep and subagent.translations == {"ru": "субагент", "de": "Subagent"}
    assert pipe.translations == {"ru": "конвейер"}
    assert glossary.languages() == ["de", "ru"]


def test_find_ignores_case():
    glossary = parse_glossary(SAMPLE)
    assert glossary.find("CLAUDE code").term == "Claude Code"
    assert glossary.find("missing") is None


def test_file_without_a_table_is_an_empty_glossary():
    assert parse_glossary("# Glossary\n\nNothing yet.\n").entries == []


def test_round_trip(tmp_path):
    glossary = parse_glossary(SAMPLE)
    path = tmp_path / "T1.glossary.md"
    path.write_text(to_markdown(glossary), encoding="utf-8")
    assert load_glossary(path) == glossary


@pytest.mark.parametrize("table, message", [
    ("| term | ru |\n|---|---|\n| a | b |\n", "'keep' column"),
    ("| term | keep | russian |\n|---|---|---|\n", "neither term, keep, notes nor a language code"),
    ("| term | keep |\n| a | yes |\n", "separator row"),
    ("| term | keep |\n|---|---|\n| a | maybe |\n", "line 3: keep must be yes or no"),
    ("| term | keep |\n|---|---|\n| a | yes | extra |\n", "line 3: 3 cells"),
    ("| term | keep |\n|---|---|\n| API | yes |\n| api | no |\n", "line 4: 'api' is already defined"),
    ("| term | keep |\n|---|---|\n|  | yes |\n", "empty term"),
    ("| term | keep | ru | ru |\n|---|---|---|---|\n", "duplicate column"),
])
def test_format_errors_name_the_line(table, message):
    with pytest.raises(GlossaryError, match=message):
        parse_glossary(table)
