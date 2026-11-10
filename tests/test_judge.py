"""
The blind audio judge against a fake `google.genai` package: option shuffling
and dedup, prompt order, answers, resume, quota stop, the filled file, the
calibration helpers and the CLI. No network, no real key.
"""

import json
import sys
from enum import Enum
from types import ModuleType, SimpleNamespace

import pytest
from typer.testing import CliRunner

from talk_autopsy import adjudicate as adj
from talk_autopsy import cli, config, judge, ref_glossary
from talk_autopsy.adjudicate import Choice, Option, Span, build_consensus, parse_choices, resolve
from talk_autopsy.glossary import load_glossary
from talk_autopsy.segments import Segment, Transcript, Word

runner = CliRunner()
ENGINES = ["faster-whisper", "assemblyai", "gemini"]
README = """# Test set

## Reference conventions

How the reference transcripts are written.

| Case | Rule |
|---|---|
| Slash commands | written as typed: `/theme` |
| Fillers `uh`, `um` | not written. *In code:* removed by the metrics normalizer |
| Numbers | digits |

## File naming
"""
CONVENTIONS = judge.read_conventions(README)


def backbone(text: str) -> Transcript:
    words = [Word(start=i * 1.0, end=i * 1.0 + 0.5, text=w, probability=0.9)
             for i, w in enumerate(text.split())]
    return Transcript(segments=[Segment(id=1, start=0, end=len(words), text=text, words=words)],
                      language="en", source="faster-whisper large-v3", meta={"engine": "faster-whisper"})


def hyp(text: str, engine: str) -> Transcript:
    return Transcript(segments=[Segment(id=1, start=0, end=1, text=text)], language="en", source=engine,
                      meta={"engine": engine})


def two_spans():
    """Span 1: majority (cloud | Claude, B+C pre-marked); span 2: split (build | built | bill)."""
    return build_consensus(
        "T9", backbone("we open cloud code here today and then we run make build in the shell"),
        [hyp("we open Claude Code here today and then we run make built in the shell", "assemblyai"),
         hyp("we open Claude Code here today and then we run make bill in the shell", "gemini")],
        30.0, ENGINES)


# --- fake google.genai ---------------------------------------------------------


class FinishReason(Enum):
    STOP = "STOP"
    MAX_TOKENS = "MAX_TOKENS"


class APIError(Exception):
    def __init__(self, code, status, message=""):
        super().__init__(f"{code} {status} {message}")
        self.code, self.status, self.message, self.details = code, status, message, None


class ClientError(APIError):
    pass


class ServerError(APIError):
    pass


def answer(heard: str, choice: str, final_text: str = "", finish=FinishReason.STOP):
    text = json.dumps({"heard": heard, "choice": choice, "final_text": final_text})
    return SimpleNamespace(text=text, candidates=[SimpleNamespace(finish_reason=finish)],
                           model_version="fake-3.8", usage_metadata=SimpleNamespace(
                               prompt_token_count=300, candidates_token_count=40, thoughts_token_count=100))


@pytest.fixture
def fake_genai(monkeypatch):
    """`state.answers`: a list consumed per call (a response or an exception); calls are recorded."""
    state = SimpleNamespace(answers=[], calls=[])

    def generate_content(model, contents, config):
        state.calls.append(SimpleNamespace(model=model, contents=contents, config=config.kwargs))
        item = state.answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return item(contents[1]) if callable(item) else item

    class Client:
        def __init__(self, api_key):
            self.models = SimpleNamespace(generate_content=generate_content)

    class Config:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Part:
        @staticmethod
        def from_bytes(data, mime_type):
            return SimpleNamespace(data=data, mime_type=mime_type)

    genai = ModuleType("google.genai")
    genai.Client = Client
    types = ModuleType("google.genai.types")
    types.GenerateContentConfig = Config
    types.FinishReason = FinishReason
    types.Part = Part
    errors = ModuleType("google.genai.errors")
    errors.APIError, errors.ClientError, errors.ServerError = APIError, ClientError, ServerError
    for name, module in (("google.genai", genai), ("google.genai.types", types),
                         ("google.genai.errors", errors)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test-key")
    monkeypatch.setattr(judge, "RETRY_WAIT_SECONDS", (0, 0))
    return state


def option_number(prompt: str, text: str) -> str:
    """The number the prompt shows for an option text."""
    for line in prompt.split("Options for the marked part:")[1].splitlines():
        if line.endswith(f". {text}"):
            return line.split(".")[0]
    raise AssertionError(f"{text!r} not shown")


def pick(text: str, heard: str = "we run make build in the shell"):
    return lambda prompt: answer(heard, option_number(prompt, text))


@pytest.fixture
def clips(tmp_path):
    result = two_spans()
    clip_dir = tmp_path / "clips"
    clip_dir.mkdir()
    for span in result.listed:
        (clip_dir / adj.clip_name(span)).write_bytes(b"RIFF fake wav " + bytes([span.number]))
    return result, clip_dir


def run(result, clip_dir, out, **kwargs):
    return judge.run("T9", result, clip_dir, CONVENTIONS, out, "gemini-3.8-flash", pace_seconds=0,
                     sleep=lambda s: None, **kwargs)


# --- options, seed, prompt -----------------------------------------------------


def test_options_are_deduplicated_by_normalized_tokens_and_keep_their_labels():
    span = Span(0, 1, "split", [Option("A", ["faster-whisper"], "Make build.", 2),
                                Option("B", ["assemblyai"], "make build", 2),
                                Option("C", ["gemini"], "make bill", 2)])
    shown = judge.shown_options(span, seed=1)
    assert len(shown) == 2
    merged = next(o for o in shown if o.labels == ["A", "B"])
    assert merged.engines == ["faster-whisper", "assemblyai"] and merged.text == "Make build."


def test_shuffle_is_stable_per_span_and_varies_across_spans():
    span = Span(0, 1, "split", [Option(label, [e], f"word{label}", 1)
                                for label, e in zip("ABC", ENGINES, strict=True)])
    first = [o.labels for o in judge.shown_options(span, judge.span_seed("T9", 1))]
    assert first == [o.labels for o in judge.shown_options(span, judge.span_seed("T9", 1))]
    orders = {tuple(tuple(o.labels) for o in judge.shown_options(span, judge.span_seed("T9", n)))
              for n in range(1, 30)}
    assert len(orders) > 1  # not always the engine order
    assert [o.number for o in judge.shown_options(span, 5)] == [1, 2, 3]


def test_prompt_asks_for_the_transcription_before_the_options_and_names_no_engine():
    result = two_spans()
    span = result.listed[1]
    options = judge.shown_options(span, judge.span_seed("T9", span.number))
    prompt = judge.build_prompt(span, options, CONVENTIONS)
    transcribe = prompt.index("Transcribe the whole audio clip")
    listed = prompt.index("Options for the marked part:")
    assert transcribe < listed
    assert all(prompt.index(f"{o.number}. {o.text}") > listed for o in options)
    assert "may be wrong" in prompt and "Agreeing with the majority" in prompt
    for engine in ENGINES + ["whisper", "AssemblyAI", "Gemini"]:
        assert engine not in prompt
    assert '"and then we run make"' in prompt and '"in the shell"' in prompt  # context to locate the span
    assert "- Slash commands: written as typed: `/theme`." in prompt
    assert "In code" not in prompt


def test_schema_puts_heard_before_choice_and_limits_the_choice():
    schema = judge.answer_schema(3)
    assert list(schema["properties"]) == ["heard", "choice", "final_text"]
    assert schema["properties"]["choice"]["enum"] == ["1", "2", "3", "none"]


def test_empty_variant_is_shown_as_nothing_said_and_context_at_the_edge():
    span = Span(0, 0, "majority", [Option("A+C", ["faster-whisper", "gemini"], adj.NO_WORDS, 0),
                                   Option("B", ["assemblyai"], "really", 1)], premarked=0, number=1,
                context_before="", context_after="now")
    prompt = judge.build_prompt(span, judge.shown_options(span, 3), CONVENTIONS)
    assert judge.EMPTY_OPTION in prompt and "the start of the clip" in prompt


def test_conventions_are_read_without_in_code_notes():
    assert CONVENTIONS == ["Slash commands: written as typed: `/theme`.", "Fillers `uh`, `um`: not written.",
                           "Numbers: digits."]
    with pytest.raises(judge.JudgeError):
        judge.read_conventions("# nothing here\n")


def test_parse_answer_checks_shape_and_order():
    good = judge.parse_answer('{"heard": "a b", "choice": "2", "final_text": "b"}', 2)
    assert good.heard_first and good.choice == "2"
    late = judge.parse_answer('{"choice": "NONE", "heard": "a b", "final_text": "c"}', 2)
    assert not late.heard_first and late.choice == "none"
    for bad in ("", "{", '{"heard": "a"}', '{"heard": "a", "choice": "3", "final_text": ""}'):
        with pytest.raises(judge.JudgeError):
            judge.parse_answer(bad, 2)


def test_final_text_follows_the_conventions_in_code():
    assert judge.clean_text("um so we, uh, run  /theme") == "so we, run /theme"
    assert judge.clean_text(adj.NO_WORDS) == "" and judge.clean_text("Hmm.") == ""


def test_none_with_empty_text_ticks_the_empty_option_or_types_no_words():
    empty = judge.ShownOption(1, "", ["A+C"], ["faster-whisper", "gemini"])
    word = judge.ShownOption(2, "really", ["B"], ["assemblyai"])
    nothing = judge.Answer("x", "none", "uh", True)
    assert judge.decision_of([empty, word], nothing)["label"] == "A+C"
    assert judge.decision_of([word], nothing)["other"] == adj.NO_WORDS
    assert judge.decision_of([word], judge.Answer("x", "none", "um really", True))["other"] == "really"
    assert judge.decision_of([empty, word], judge.Answer("x", "2", "", True))["label"] == "B"


# --- the run -------------------------------------------------------------------


def test_run_judges_every_span_and_records_it(fake_genai, clips, tmp_path):
    result, clip_dir = clips
    out = tmp_path / "judge.jsonl"
    fake_genai.answers = [pick("Claude", "we open Claude Code here"), pick("build")]
    summary = run(result, clip_dir, out)
    assert (summary.done, summary.remaining, summary.failed) == (2, 0, [])
    records = judge.load_records(out)
    assert [r["span"] for r in records] == [1, 2]
    assert records[0]["decision"]["label"] == "B+C" and records[1]["decision"]["label"] == "A"
    assert records[0]["heard_contains_chosen"] is True and records[1]["heard_contains_chosen"] is True
    first = records[0]
    fields = {"model", "seed", "heard", "options", "choice", "final_text", "latency_seconds", "tokens"}
    assert fields <= set(first)
    assert first["tokens"] == {"prompt": 300, "output": 40, "thinking": 100}
    call = fake_genai.calls[0]
    assert call.model == "gemini-3.8-flash"
    assert call.contents[0].mime_type == "audio/wav" and call.contents[0].data.startswith(b"RIFF")
    assert call.config["response_json_schema"]["properties"]["choice"]["enum"][-1] == "none"
    stats = judge.run_stats(records)
    assert stats["gemini_chosen"] == 1 and stats["none"] == 0 and stats["consistency_share"] == 1.0


def test_resume_after_an_interruption_judges_only_the_rest(fake_genai, clips, tmp_path):
    result, clip_dir = clips
    out = tmp_path / "judge.jsonl"
    fake_genai.answers = [pick("Claude")]
    assert run(result, clip_dir, out, limit=1).remaining == 1
    with pytest.raises(judge.JudgeError, match="--resume"):
        run(result, clip_dir, out)
    fake_genai.answers = [pick("bill")]
    summary = run(result, clip_dir, out, resume=True)
    assert (summary.done_before, summary.judged_now, summary.remaining) == (1, 1, 0)
    assert [r["span"] for r in judge.load_records(out)] == [1, 2]
    assert len(fake_genai.calls) == 2
    with pytest.raises(judge.JudgeError, match="same --model"):
        judge.run("T9", result, clip_dir, CONVENTIONS, out, "other-model", resume=True, pace_seconds=0)


def test_daily_quota_stops_the_run_and_keeps_what_was_judged(fake_genai, clips, tmp_path):
    result, clip_dir = clips
    out = tmp_path / "judge.jsonl"
    fake_genai.answers = [pick("Claude"), ClientError(429, "RESOURCE_EXHAUSTED", "GenerateRequestsPerDay")]
    summary = run(result, clip_dir, out)
    assert summary.stopped and "daily quota" in summary.stopped
    assert (summary.done, summary.remaining) == (1, 1)
    assert len(judge.load_records(out)) == 1


def test_server_errors_are_retried_then_the_span_is_left_for_later(fake_genai, clips, tmp_path):
    result, clip_dir = clips
    out = tmp_path / "judge.jsonl"
    busy = ServerError(503, "UNAVAILABLE")
    fake_genai.answers = [busy, pick("Claude"), busy, busy, busy, busy, busy, busy]
    summary = run(result, clip_dir, out)
    assert summary.judged_now == 1 and summary.failed == [2] and summary.remaining == 1


def test_a_malformed_answer_gets_one_more_attempt(fake_genai, clips, tmp_path):
    result, clip_dir = clips
    out = tmp_path / "judge.jsonl"
    cut = answer("we open", "1", finish=FinishReason.MAX_TOKENS)
    fake_genai.answers = [cut, pick("Claude"), pick("build")]
    assert run(result, clip_dir, out).done == 2


def test_missing_clips_are_an_error_before_any_call(fake_genai, clips, tmp_path):
    result, clip_dir = clips
    next(clip_dir.iterdir()).unlink()
    with pytest.raises(judge.JudgeError, match="clips missing"):
        run(result, clip_dir, tmp_path / "judge.jsonl")
    assert fake_genai.calls == []


# --- the filled file -------------------------------------------------------------


def test_filled_file_ticks_the_judges_choices_and_resolves(fake_genai, clips, tmp_path):
    result, clip_dir = clips
    out = tmp_path / "judge.jsonl"
    fake_genai.answers = [lambda prompt: answer("we open Claude Code", "none", "uh Claude"),
                          pick("bill")]
    run(result, clip_dir, out)
    original = adj.adjudication_markdown(result, "work/T9/clips")
    filled = judge.filled_markdown(original, judge.load_records(out), 2, "2026-11-10")
    assert "blind audio judge" in filled and "Spans judged: 2 of 2" in filled
    choices = parse_choices(filled)
    assert choices[1] == Choice([], "Claude")  # the pre-marked B+C is unticked
    assert choices[2] == Choice(["C"], "")
    assert resolve(result, choices).reference.startswith("we open Claude Code here")
    assert "make bill in" in resolve(result, choices).reference
    assert original == adj.adjudication_markdown(result, "work/T9/clips")


def test_unjudged_spans_are_left_unticked_so_resolve_refuses(fake_genai, clips, tmp_path):
    result, clip_dir = clips
    out = tmp_path / "judge.jsonl"
    fake_genai.answers = [pick("Claude")]
    run(result, clip_dir, out, limit=1)
    filled = judge.filled_markdown(adj.adjudication_markdown(result, "c"), judge.load_records(out), 2, "d")
    assert parse_choices(filled)[2] == Choice([], "")
    with pytest.raises(adj.AdjudicationError, match="nothing chosen"):
        resolve(result, parse_choices(filled))


def test_other_no_words_resolves_to_an_empty_variant():
    result = two_spans()
    choices = {1: Choice([], adj.NO_WORDS), 2: Choice(["A"], "")}
    resolution = resolve(result, choices)
    assert resolution.reference.startswith("we open Code here")
    assert resolution.protocol["spans"][0]["words"] == 0


# --- calibration helpers ---------------------------------------------------------


def span(verdict, *texts, premarked=None, number=1):
    labels, engines = ["A", "B", "C"][:len(texts)], ENGINES[:len(texts)]
    options = [Option(lab, [e], t, 1) for lab, e, t in zip(labels, engines, texts, strict=True)]
    return Span(0, 1, verdict, options, premarked=premarked, number=number)


def test_classify_decision():
    majority = span("majority", "great", "what we like", premarked=1)
    assert judge.classify_decision(majority, Choice(["B"], "")) == "majority"
    assert judge.classify_decision(majority, Choice([], "what we like")) == "majority"
    assert judge.classify_decision(majority, Choice(["A"], "")) == "ear"
    product = span("majority", "Quadcode", "Claude Code", premarked=0)
    assert judge.classify_decision(product, Choice(["B"], "")) == "rule"
    digits = span("majority", "two or three", "2 or three", premarked=0)
    assert judge.classify_decision(digits, Choice([], "2 or 3")) == "rule"
    term = span("split", "using an LM", "using a LM", "an elem")
    assert judge.classify_decision(term, Choice([], "using an LLM"), ["LLM"]) == "rule"
    assert judge.classify_decision(term, Choice([], "using an LLM")) == "ear"


def test_compare_counts_agreement_by_class_and_verdict():
    result = two_spans()
    reference = {1: Choice(["B+C"], ""), 2: Choice([], "make build")}
    judged = {1: Choice([], "Claude"), 2: Choice(["B"], "")}
    report = judge.compare(result, reference, judged)
    assert report["overall"] == {"spans": 2, "agree": 1, "share": 0.5}
    assert report["by_verdict"]["majority"]["agree"] == 1 and report["by_verdict"]["split"]["agree"] == 0
    assert [r["agree"] for r in report["rows"]] == [True, False]


# --- CLI -------------------------------------------------------------------------


def test_cli_judge_writes_the_log_and_the_judged_copy(fake_genai, clips, tmp_path, monkeypatch):
    result, clip_dir = clips
    test_set, work = tmp_path / "test-set", tmp_path / "work"
    (work / "T9").mkdir(parents=True)
    test_set.mkdir()
    clip_dir.rename(work / "T9" / "clips")
    monkeypatch.setattr(config, "TEST_SET_DIR", test_set)
    monkeypatch.setattr(config, "WORK_DIR", work)
    monkeypatch.setattr(judge, "PACE_SECONDS", 0.0)
    (test_set / "README.md").write_text(README, encoding="utf-8")
    (work / "T9" / "consensus.json").write_text(adj.consensus_to_json(result), encoding="utf-8")
    original = adj.adjudication_markdown(result, "work/T9/clips", link_prefix="../../")
    (test_set / "T9.adjudication.md").write_text(original, encoding="utf-8")

    fake_genai.answers = [pick("Claude", "secret words")]
    first = runner.invoke(cli.app, ["judge", "T9", "--limit", "1", "--pace", "0"])
    assert first.exit_code == 2, first.output
    assert "1 of 2 spans judged" in first.output and "--resume" in first.output
    assert "secret words" not in first.output  # counts only, never the content
    fake_genai.answers = [pick("build", "secret words")]
    second = runner.invoke(cli.app, ["judge", "T9", "--resume", "--pace", "0"])
    assert second.exit_code == 0, second.output
    assert "2 of 2 spans judged" in second.output and "secret" not in second.output
    assert (test_set / "T9.adjudication.md").read_text(encoding="utf-8") == original
    judged = test_set / "T9.adjudication.judged.md"
    resolved = runner.invoke(cli.app, ["resolve", "T9", "--adjudication", str(judged),
                                       "--out", str(tmp_path / "judged.ref.txt")])
    assert resolved.exit_code == 0, resolved.output
    assert (tmp_path / "judged.ref.txt").read_text(encoding="utf-8").startswith("we open Claude Code")
    compared = runner.invoke(cli.app, ["judge-compare", "T9"])
    assert compared.exit_code == 1  # the original split is undecided: nothing to compare against
    assert "0 options ticked" in compared.output


# --- reference glossary by Gemini ------------------------------------------------

REFERENCE = "We open Claude Code and run npm install. Then GitHub Actions run the pull requests."


def test_glossary_terms_must_occur_in_the_reference():
    proposed = [{"term": "Claude Code", "keep": True, "notes": "product"},
                {"term": "claude code", "keep": True, "notes": "duplicate"},
                {"term": "Claude", "keep": True, "notes": "only inside Claude Code"},
                {"term": "pull request", "keep": False, "notes": "plural in the text"},
                {"term": "Kubernetes", "keep": True, "notes": "not spoken"},
                {"term": "  ", "keep": True, "notes": ""}]
    entries, absent, over = ref_glossary.select_terms(REFERENCE, proposed)
    assert [e.term for e in entries] == ["Claude Code", "pull request"]
    assert (absent, over) == (2, 0)


def test_glossary_is_capped(monkeypatch):
    monkeypatch.setattr(ref_glossary, "MAX_TERMS", 1)
    entries, absent, over = ref_glossary.select_terms(REFERENCE, [{"term": "npm", "keep": True},
                                                                  {"term": "GitHub", "keep": True}])
    assert [e.term for e in entries] == ["npm"] and over == 1


def test_cli_glossary_ref_writes_the_glossary_and_prints_counts(fake_genai, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TEST_SET_DIR", tmp_path)
    (tmp_path / "T9.ref.txt").write_text(REFERENCE, encoding="utf-8")
    terms = [{"term": "npm", "keep": True, "notes": "tool"}, {"term": "Docker", "keep": True, "notes": "x"}]
    fake_genai.answers = [SimpleNamespace(text=json.dumps({"terms": terms}), candidates=[
        SimpleNamespace(finish_reason=FinishReason.STOP)], usage_metadata=SimpleNamespace(
        prompt_token_count=100, candidates_token_count=10, thoughts_token_count=0))]
    result = runner.invoke(cli.app, ["glossary-ref", "T9"])
    assert result.exit_code == 0, result.output
    assert "1 terms (2 proposed" in result.output and "npm" not in result.output
    glossary = load_glossary(tmp_path / "T9.glossary.md")
    assert [(e.term, e.keep) for e in glossary.entries] == [("npm", True)]
    assert REFERENCE in fake_genai.calls[0].contents[0]
    assert runner.invoke(cli.app, ["glossary-ref", "T9"]).exit_code == 1  # no silent overwrite
