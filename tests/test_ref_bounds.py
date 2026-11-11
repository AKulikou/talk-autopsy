"""
Two references from the judge's records (J: the judge everywhere, M: the engines'
majority where it exists), the uncertain spans, and the bounded term accuracy of
the T6 gate. Fake references and fake judge records only: no T6 data.
"""

import json

import pytest
from typer.testing import CliRunner

from talk_autopsy import adjudicate as adj
from talk_autopsy import cli, config, metrics, ref_bounds
from talk_autopsy.adjudicate import Choice, build_consensus, resolve
from talk_autopsy.glossary import Glossary, GlossaryEntry, to_markdown
from talk_autopsy.segments import Segment, Transcript, Word

runner = CliRunner()
ENGINES = ["faster-whisper", "assemblyai", "gemini"]


# --- resolve keeps each span's range ---------------------------------------------------


def backbone(text: str, pauses: dict[int, float] | None = None) -> Transcript:
    words, t = [], 0.0
    for i, w in enumerate(text.split()):
        t += (pauses or {}).get(i, 0.0)
        words.append(Word(start=t, end=t + 0.5, text=w, probability=0.9))
        t += 1.0
    return Transcript(segments=[Segment(id=1, start=0, end=t, text=text, words=words)], language="en",
                      source="faster-whisper large-v3", meta={"engine": "faster-whisper"})


def hyp(text: str, engine: str) -> Transcript:
    return Transcript(segments=[Segment(id=1, start=0, end=1, text=text)], language="en", source=engine,
                      meta={"engine": engine})


A = "alpha cloud code beta gamma delta epsilon make build zeta eta theta iota push to GitHub kappa"
B = "alpha Claude Code beta gamma delta epsilon make built zeta eta theta iota push to get hub kappa"
C = "alpha Claude Code beta gamma delta epsilon make bill zeta eta theta iota push to get hub kappa"


def three_spans(pauses=None):
    """Span 1 majority (cloud | Claude), span 2 split (build | built | bill), span 3 majority (get hub)."""
    return build_consensus("T9", backbone(A, pauses), [hyp(B, "assemblyai"), hyp(C, "gemini")], 30.0, ENGINES)


def test_resolve_records_the_character_range_of_every_listed_span():
    result = three_spans(pauses={4: 3.0})  # a paragraph break before "gamma"
    choices = {1: Choice(["B+C"], ""), 2: Choice([], "build"), 3: Choice([], adj.NO_WORDS)}
    resolution = resolve(result, choices)
    text = resolution.reference
    assert "\n\n" in text
    assert text[slice(*resolution.ranges[1])] == "Claude"
    assert text[slice(*resolution.ranges[2])] == "build"
    start, end = resolution.ranges[3]
    assert start == end and text[:start].endswith("push to")  # empty span: an empty range in place


# --- two references from judge records ---------------------------------------------------


def record(span: int, verdict: str, final_text: str, choice: str = "none", chosen: str = "",
           model: str = "gemini-4-carbon") -> dict:
    return {"span": span, "verdict": verdict, "model": model, "choice": choice, "final_text": final_text,
            "decision": {"text": chosen if choice != "none" else final_text}}


def judged_records():
    return [record(1, "majority", "Claude", choice="1", chosen="Claude"),  # agrees with the majority
            record(2, "split", "build"),
            record(3, "majority", "GitHub")]  # overrides the majority "get hub"


def test_j_takes_the_judge_everywhere_and_m_the_majority_where_one_exists():
    refs = ref_bounds.build(three_spans(), judged_records())
    assert "push to GitHub kappa" in refs.j.reference and "make build zeta" in refs.j.reference
    assert "push to get hub kappa" in refs.m.reference and "make build zeta" in refs.m.reference
    assert "alpha Claude Code beta" in refs.j.reference and "alpha Claude Code beta" in refs.m.reference
    assert [(u["span"], u["verdict"]) for u in refs.uncertain] == [(2, "split"), (3, "majority")]
    c = refs.counts
    assert c["judged"] == {"majority": 2, "split": 1}
    assert (c["majority_overridden"], c["uncertain_spans"]) == (1, 2)
    assert c["wer_j_m"]["substitutions"] + c["wer_j_m"]["insertions"] + c["wer_j_m"]["deletions"] > 0


def test_j_and_m_differ_only_inside_uncertain_spans():
    refs = ref_bounds.build(three_spans(), judged_records())

    def outside(text: str, key: str) -> list[str]:
        tokens = metrics.tokenize(text)
        inside = {i for u in refs.uncertain for i in range(*u[key]["words"])}
        return [t for i, t in enumerate(tokens) if i not in inside]

    assert metrics.tokenize(refs.j.reference) != metrics.tokenize(refs.m.reference)
    assert outside(refs.j.reference, "J") == outside(refs.m.reference, "M")
    for u in refs.uncertain:
        assert metrics.tokenize(refs.j.reference)[slice(*u["J"]["words"])] == metrics.tokenize(
            refs.j.reference[slice(*u["J"]["chars"])])


def test_the_judges_text_passes_through_the_adjudication_normaliser():
    records = judged_records()
    records[1] = record(2, "split", "build build")  # an immediate repetition is written once
    refs = ref_bounds.build(three_spans(), records)
    assert "make build zeta" in refs.j.reference and "make build zeta" in refs.m.reference


def test_incomplete_or_mixed_records_are_refused():
    with pytest.raises(ref_bounds.BoundsError, match="not judged yet"):
        ref_bounds.build(three_spans(), judged_records()[:2])
    mixed = judged_records()
    mixed[0]["model"] = "gemini-3.8-flash"
    with pytest.raises(ref_bounds.BoundsError, match="2 models"):
        ref_bounds.build(three_spans(), mixed)
    with pytest.raises(ref_bounds.BoundsError, match="twice"):
        ref_bounds.build(three_spans(), [*judged_records(), record(2, "split", "built")])


def test_uncertain_file_holds_no_text_and_is_tied_to_its_references(tmp_path):
    refs = ref_bounds.build(three_spans(), judged_records())
    text = ref_bounds.uncertain_json("T9", refs, "gemini-4-carbon", "j.txt", "m.txt", "2026-11-11")
    for word in ("GitHub", "build", "Claude", "hub"):
        assert word not in text
    path = tmp_path / "uncertain_spans.json"
    path.write_text(text, encoding="utf-8")
    ranges = ref_bounds.load_uncertain(path, refs.j.reference, refs.m.reference)
    assert ranges == [tuple(u["J"]["words"]) for u in refs.uncertain]
    with pytest.raises(ref_bounds.BoundsError, match="another"):
        ref_bounds.load_uncertain(path, refs.j.reference + "x", refs.m.reference)


# --- CLI: judge-refs and the sealed eval print counts only -------------------------------


@pytest.fixture
def sealed_project(tmp_path, monkeypatch):
    test_set, work = tmp_path / "test-set", tmp_path / "work"
    (work / "T9").mkdir(parents=True)
    test_set.mkdir()
    monkeypatch.setattr(config, "TEST_SET_DIR", test_set)
    monkeypatch.setattr(config, "WORK_DIR", work)
    monkeypatch.setattr(config, "SEALED_FRAGMENTS", frozenset({"T9"}))
    (work / "T9" / "consensus.json").write_text(adj.consensus_to_json(three_spans()), encoding="utf-8")
    (work / "T9" / "judge.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in judged_records()), encoding="utf-8")
    return test_set, work


SECRET_WORDS = ("Claude", "GitHub", "build", "alpha", "kappa", "hub")


def test_cli_judge_refs_writes_both_references_and_prints_counts_only(sealed_project):
    test_set, work = sealed_project
    result = runner.invoke(cli.app, ["judge-refs", "T9"])
    assert result.exit_code == 0, result.output
    assert "majority 2, split 1" in result.output and "overrode the majority: 1" in result.output
    assert "uncertain spans (split + overridden majority): 2" in result.output
    assert not any(word in result.output for word in SECRET_WORDS)
    assert "GitHub" in (test_set / "T9.ref.txt").read_text(encoding="utf-8")
    assert "get hub" in (test_set / "T9.ref.majority.txt").read_text(encoding="utf-8")
    assert (work / "T9" / "uncertain_spans.json").is_file()
    glossary = Glossary(entries=[GlossaryEntry(term="Claude Code", keep=True),
                                 GlossaryEntry(term="GitHub", keep=True)])
    (test_set / "T9.glossary.md").write_text(to_markdown(glossary, languages=["ru"]), encoding="utf-8")
    again = runner.invoke(cli.app, ["judge-refs", "T9"])  # same text: no --force needed
    assert again.exit_code == 0, again.output
    assert "glossary: 2 terms; 2 occurrences in J, 1 inside uncertain spans" in again.output
    (test_set / "T9.ref.txt").write_text("changed by hand\n", encoding="utf-8")
    assert runner.invoke(cli.app, ["judge-refs", "T9"]).exit_code == 1  # no silent overwrite
