"""Auto-glossary against the fake Claude client: evidence rule, output format, coverage, merge."""

import datetime as dt

from talk_autopsy import glossary_auto
from talk_autopsy.glossary import Glossary, GlossaryEntry, parse_glossary
from talk_autopsy.glossary_auto import AutoGlossaryAnswer, AutoTerm
from talk_autopsy.segments import Segment, Transcript
from tests.conftest import response


def transcript(*texts: str) -> Transcript:
    return Transcript(segments=[Segment(id=n, start=n, end=n + 1, text=t) for n, t in enumerate(texts, 1)],
                      language="en", source="assemblyai universal-3-5-pro", meta={})


def term(name, heard_as=(), keep=True, translation="", kind="product", note=""):
    return AutoTerm(term=name, heard_as=list(heard_as), kind=kind, keep=keep, translation=translation,
                    note=note)


TALK = transcript("Today I show Quadcode, our agentic coding tool.",
                  "You run it in tmux or in VS code.",
                  "Open a pull request and Quad code reviews it.")


def test_generate_keeps_evidence_drops_inventions(fake_claude):
    answer = AutoGlossaryAnswer(terms=[
        term("Claude Code", ["Quadcode", "Quad code", "Cloud Code"]),  # last form is not in the text
        term("tmux", kind="tool"),
        term("VS Code", kind="tool"),
        term("pull request", keep=False, translation="пул-реквест", kind="technical term"),
        term("Kubernetes", kind="tool"),  # invented: no evidence
        term("tmux", kind="tool"),  # duplicate
    ])
    fake = fake_claude(lambda kwargs: response(answer, input_tokens=2000, output_tokens=800))
    result = glossary_auto.generate(TALK, "ru", "Title: a talk", model="claude-haiku-5-5")

    request = fake.requests[0]
    assert request["model"] == "claude-haiku-5-5"
    assert "Target language\n\nru" in request["system"]
    assert "Video context:\nTitle: a talk" in request["messages"][0]["content"]
    assert "Quadcode, our agentic coding tool." in request["messages"][0]["content"]

    terms = [e.term for e in result.glossary.entries]
    assert terms == ["Claude Code", "tmux", "VS Code", "pull request"]
    assert result.heard_as == {"Claude Code": ["Quadcode", "Quad code"]}
    assert result.dropped == ["Kubernetes (not in the transcript)"]
    pull = result.glossary.find("pull request")
    assert not pull.keep and pull.translations == {"ru": "пул-реквест"}
    assert "heard as: Quadcode, Quad code" in result.glossary.find("Claude Code").notes
    assert result.cost_usd > 0


def test_markdown_round_trips_with_the_header():
    codebase = GlossaryEntry(term="codebase", keep=False, translations={"ru": "кодовая база"})
    glossary = Glossary(entries=[GlossaryEntry(term="Claude Code", keep=True, notes="product"), codebase])
    text = glossary_auto.markdown(glossary, "claude-haiku-5-5", "ru", dt.date(2026, 11, 9))
    assert text.startswith("<!-- auto-generated: claude-haiku-5-5, 2026-11-09 -->\n")
    assert parse_glossary(text) == glossary


def test_validate_rejects_empty_and_oversized_answers():
    assert glossary_auto.validate(AutoGlossaryAnswer(terms=[])) == ["no terms"]
    many = AutoGlossaryAnswer(terms=[term(f"t{i}") for i in range(61)])
    assert glossary_auto.validate(many) == ["61 terms, at most 60"]
    assert glossary_auto.validate(AutoGlossaryAnswer(terms=[term("tmux")])) == []


def test_over_long_terms_are_dropped():
    answer = AutoGlossaryAnswer(terms=[term("one two three four five six seven")])
    glossary, dropped, _ = glossary_auto.build_glossary(answer, "one two three four five six seven", "ru")
    assert glossary.entries == [] and dropped == ["one two three four five six seven (over 6 words)"]


def test_occurs_ignores_case_and_spaces_but_not_word_parts():
    assert glossary_auto.occurs("quad  code", "the Quad code tool")
    assert not glossary_auto.occurs("code", "a codebase")
    assert glossary_auto.occurs("diff", "look at the diffs")
    assert glossary_auto.occurs("pull request", "two pull requests")


def test_coverage_uses_the_metric_spelling_rule():
    reference = Glossary(entries=[GlossaryEntry(term=t, keep=True) for t in
                                  ("Claude Code", "macOS", "pull request", "tmux", "Claude")])
    auto = Glossary(entries=[GlossaryEntry(term=t, keep=True) for t in
                             ("claude code", "MacOS", "pull requests", "Kubernetes", "Claude")])
    c = glossary_auto.coverage(auto, reference)
    # "Claude Code" is not an identifier, so case does not matter; "macOS" is one.
    assert c.covered == ["Claude Code", "pull request", "Claude"]
    assert c.wrong_case == [("macOS", "MacOS")]
    assert c.missing == ["tmux"]
    assert c.noise == ["Kubernetes"]
    assert c.share == 3 / 5


def test_merge_user_glossary_wins():
    auto = Glossary(entries=[GlossaryEntry(term="codebase", keep=False, translations={"ru": "кодовая база"}),
                             GlossaryEntry(term="tmux", keep=True)])
    user = Glossary(entries=[GlossaryEntry(term="Codebase", keep=True, notes="say it in English")])
    merged = glossary_auto.merge(auto, user)
    assert [e.term for e in merged.entries] == ["Codebase", "tmux"]
    assert merged.entries[0].keep
    assert glossary_auto.merge(auto, None).entries == auto.entries


def test_read_context(tmp_path):
    path = tmp_path / "context.txt"
    path.write_text("Title: Building agents\n", encoding="utf-8")
    assert glossary_auto.read_context(str(path)) == "Title: Building agents"
    assert glossary_auto.read_context("Title: inline") == "Title: inline"
    assert glossary_auto.read_context(None) == ""
