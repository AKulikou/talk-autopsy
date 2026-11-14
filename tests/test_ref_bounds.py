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


# --- bounded term accuracy on fake references ----------------------------------------


def text_with(terms: dict[str, int], filler: str = "and") -> str:
    """`n` occurrences of each term, separated by a filler word."""
    return f" {filler} ".join(term for term, n in terms.items() for _ in range(n))


def test_pass_not_demonstrated_when_the_lower_bound_reaches_the_threshold_on_few_occurrences():
    j = text_with({"Docker": 10, "Kubernetes": 1})  # Docker at tokens 0, 2, ..., 18; Kubernetes at 20
    result = metrics.bounded_term_accuracy(j, j, j, ["Docker", "Kubernetes"], [(20, 21)])
    assert (result.occurrences, result.uncertain_occurrences, result.certain_hits) == (11, 1, 10)
    assert result.lower == pytest.approx(10 / 11) and result.upper == 1.0
    assert result.interval_lower[0] < 0.85 <= result.lower  # 10 of 11: the interval is wide
    assert result.verdict == metrics.PASS_NOT_DEMONSTRATED


def test_pass_demonstrated_when_the_wilson_lower_limit_reaches_the_threshold():
    j = text_with({"Docker": 100})
    result = metrics.bounded_term_accuracy(j, j, j, ["Docker"], [])
    assert result.lower == 1.0 and result.interval_lower[0] >= 0.85
    assert result.verdict == metrics.PASS_DEMONSTRATED


def test_fail_when_the_upper_bound_is_below_the_threshold():
    j = text_with({"Docker": 10, "Kubernetes": 1})
    hypothesis = text_with({"Docker": 5})
    result = metrics.bounded_term_accuracy(j, j, hypothesis, ["Docker", "Kubernetes"], [(20, 21)])
    assert result.lower == pytest.approx(5 / 11) and result.upper == pytest.approx(6 / 11)
    assert result.verdict == "fail"


def test_inconclusive_when_the_threshold_lies_between_the_bounds():
    j = text_with({"Docker": 4, "Kubernetes": 2})  # Kubernetes at tokens 8 and 10
    result = metrics.bounded_term_accuracy(j, j, j, ["Docker", "Kubernetes"], [(8, 11)])
    assert result.against_j.accuracy == 1.0
    assert result.lower == pytest.approx(4 / 6) and result.upper == 1.0
    assert result.verdict == "inconclusive"


def test_the_bounds_enclose_the_accuracy_against_j():
    j = text_with({"Docker": 3, "Kubernetes": 2, "GitHub": 2})
    for hypothesis in (j, text_with({"Docker": 1, "GitHub": 3}), "nothing"):
        for ranges in ([], [(0, 1)], [(2, 9)], [(0, 13)]):
            terms = ["Docker", "Kubernetes", "GitHub"]
            result = metrics.bounded_term_accuracy(j, j, hypothesis, terms, ranges)
            assert result.lower <= result.against_j.accuracy <= result.upper


def test_without_uncertain_spans_the_bounds_equal_the_accuracy_against_j():
    j = text_with({"Docker": 3, "Kubernetes": 2})
    hypothesis = text_with({"Docker": 2, "Kubernetes": 2})
    result = metrics.bounded_term_accuracy(j, j, hypothesis, ["Docker", "Kubernetes"], [])
    assert result.lower == result.upper == result.against_j.accuracy == pytest.approx(4 / 5)


def test_occurrence_inside_or_outside_an_uncertain_span():
    j = "we open Claude Code and run Docker now"  # Claude Code = tokens 2-3, Docker = token 6
    terms = ["Claude Code", "Docker"]

    def inside(ranges):
        return metrics.bounded_term_accuracy(j, j, j, terms, ranges).uncertain_occurrences

    assert inside([(6, 7)]) == 1  # Docker inside
    assert inside([(3, 4)]) == 1  # overlaps the second token of Claude Code: the occurrence is uncertain
    assert inside([(4, 6)]) == 0  # "and run": next to both terms, overlaps neither
    assert inside([(4, 4)]) == 0  # an empty span (nothing said) overlaps nothing
    assert inside([(0, 8)]) == 2
    assert ref_bounds.term_occurrences_inside(j, terms, [(3, 4)]) == (2, 1)


def test_term_occurrences_agree_with_term_accuracy_counts():
    text = "Claude Code and Claude and pull requests on GitHub, Github"
    terms = ["Claude", "Claude Code", "pull request", "GitHub"]
    found = metrics.term_occurrences(text, terms)
    assert [len(f) for f in found] == [1, 1, 1, 1]
    assert found[1] == [(0, 2)] and found[0] == [(3, 4)]  # the longer term claims its tokens first
    rows = metrics.term_accuracy(text, text, terms).rows
    assert [r.expected for r in rows] == [len(f) for f in found]


def test_m_is_reported_but_does_not_change_the_verdict():
    j = text_with({"Docker": 4, "Kubernetes": 2})
    m = text_with({"Docker": 4})
    result = metrics.bounded_term_accuracy(j, m, j, ["Docker", "Kubernetes"], [(8, 11)])
    assert result.against_m.accuracy == 1.0 and result.against_m.absent == ["Kubernetes"]
    assert result.verdict == "inconclusive"


def test_no_term_in_j_is_an_error():
    with pytest.raises(ValueError, match="no glossary term"):
        metrics.bounded_term_accuracy("plain words", "plain words", "plain words", ["Docker"], [])


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


def test_term_in_an_overridden_majority_span_is_uncertain():
    refs = ref_bounds.build(three_spans(), judged_records())
    ranges = [tuple(u["J"]["words"]) for u in refs.uncertain]
    terms = ["Claude Code", "GitHub"]
    j, m = refs.j.reference, refs.m.reference
    result = metrics.bounded_term_accuracy(j, m, j, terms, ranges)
    assert (result.occurrences, result.uncertain_occurrences) == (2, 1)
    assert (result.lower, result.upper, result.verdict) == (0.5, 1.0, "inconclusive")
    assert result.against_m.absent == ["GitHub"]


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


def test_cli_eval_on_a_sealed_fragment_reports_both_references_bounds_and_verdict(sealed_project, tmp_path):
    test_set, _ = sealed_project
    hypothesis = tmp_path / "hyp.txt"
    hypothesis.write_text(A.replace("cloud code", "Claude Code"), encoding="utf-8")
    (test_set / "T9.ref.txt").write_text("a reference without the bounds files\n", encoding="utf-8")
    missing = runner.invoke(cli.app, ["eval", "T9", "--hyp", str(hypothesis)])
    assert missing.exit_code == 1 and "judge-refs" in missing.output
    assert runner.invoke(cli.app, ["judge-refs", "T9", "--force"]).exit_code == 0
    no_glossary = runner.invoke(cli.app, ["eval", "T9", "--hyp", str(hypothesis)])
    assert no_glossary.exit_code == 1 and "glossary-ref" in no_glossary.output
    glossary = Glossary(entries=[GlossaryEntry(term="Claude Code", keep=True),
                                 GlossaryEntry(term="GitHub", keep=True)])
    (test_set / "T9.glossary.md").write_text(to_markdown(glossary, languages=["ru"]), encoding="utf-8")
    result = runner.invoke(cli.app, ["eval", "T9", "--hyp", str(hypothesis)])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "term accuracy against J" in out and "term accuracy against M" in out
    assert "lower bound" in out and "upper bound" in out
    assert "1 of 2 occurrences inside uncertain spans" in out
    assert "INCONCLUSIVE" in out
    assert not any(word in out.replace(str(hypothesis), "") for word in SECRET_WORDS)


def test_cli_eval_refuses_ranges_built_for_other_references(sealed_project, tmp_path):
    test_set, _ = sealed_project
    assert runner.invoke(cli.app, ["judge-refs", "T9"]).exit_code == 0
    (test_set / "T9.glossary.md").write_text(
        to_markdown(Glossary(entries=[GlossaryEntry(term="GitHub", keep=True)]), languages=["ru"]),
        encoding="utf-8")
    (test_set / "T9.ref.majority.txt").write_text("edited\n", encoding="utf-8")
    hypothesis = tmp_path / "hyp.txt"
    hypothesis.write_text("anything", encoding="utf-8")
    result = runner.invoke(cli.app, ["eval", "T9", "--hyp", str(hypothesis)])
    assert result.exit_code == 1 and "rebuild" in result.output


def test_cli_eval_bounds_scores_an_unsealed_fragment_like_t6_and_lists_misses(sealed_project, tmp_path,
                                                                                monkeypatch):
    """T7 (v1.2) is not sealed but is scored against J and M with the bounds: `eval --bounds`."""
    test_set, _ = sealed_project
    monkeypatch.setattr(config, "SEALED_FRAGMENTS", frozenset())
    assert runner.invoke(cli.app, ["judge-refs", "T9"]).exit_code == 0
    glossary = Glossary(entries=[GlossaryEntry(term="Claude Code", keep=True),
                                 GlossaryEntry(term="GitHub", keep=True)])
    (test_set / "T9.glossary.md").write_text(to_markdown(glossary, languages=["ru"]), encoding="utf-8")
    hypothesis = tmp_path / "hyp.txt"
    hypothesis.write_text(A, encoding="utf-8")  # "cloud code": the term is missed
    plain = runner.invoke(cli.app, ["eval", "T9", "--hyp", str(hypothesis)])
    assert plain.exit_code == 0 and "lower bound" not in plain.output  # without --bounds: J only
    result = runner.invoke(cli.app, ["eval", "T9", "--hyp", str(hypothesis), "--bounds"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "two references and bounds" in out and "sealed" not in out
    assert "term accuracy against M" in out and "lower bound" in out and "upper bound" in out
    assert "missed against J: Claude Code 0/1" in out
