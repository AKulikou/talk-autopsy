"""
Blind audio judge for adjudication spans (evaluation tooling, not part of the
frozen pipeline).

    T<n>.adjudication.md spans (work/T<n>/consensus.json) + clips
      -> one Gemini request per majority or split span
      -> work/T<n>/judge.jsonl + data/test-set/T<n>.adjudication.judged.md
      -> talk-autopsy resolve T<n> --adjudication <judged file>

Why: the owner (not a native speaker) has withdrawn from adjudication, and the
assistant cannot hear audio and is the corrector a hold-out run scores. The
judge listens to each clip and is kept blind in three ways:

1. **Transcribe first.** The structured answer has `heard` (a verbatim
   transcription of the whole clip) before `choice` and `final_text`, and the
   prompt asks for the transcription before it lists any option.
2. **No labels.** Options are deduplicated by their normalized tokens,
   shuffled with a seed derived from the fragment and span number, and numbered
   1..k with no engine names and no vote counts. The prompt says every option
   may be wrong and agreeing with the majority is not a goal.
3. **Context only to locate.** The backbone's context words before and after
   the span tell the judge where the marked part is.

`final_text` is cleaned with the reference conventions applied in code
(`metrics.is_filler`: fillers uh/um/er/ah/hmm removed; whitespace collapsed);
the conventions that need judgement (slash commands as typed, digits, ...) are
passed to the judge as writing rules, read from data/test-set/README.md.

A choice of an option ticks that option in the filled file; `none` writes the
judge's `final_text` after `other:`, or `other: (no words)` when nothing is
said and no option is empty. The original adjudication file is never changed.

Measured per run (`run_stats`): the share of spans where the chosen option
came from the Gemini engine (self-preference indicator, the judge is a Gemini
model too), the share of `none`, and the share where `heard` already contains
the chosen text (consistency between steps 1 and 3).

The run is resumable span by span: every judged span is appended to
judge.jsonl at once, and a rerun with `resume` skips the spans already there.
Resume is keyed by span and model: records of another model (the free-tier
pilot of 09a) are moved to `judge.free-pilot.jsonl` and never reused. A daily
quota, billing or spend-cap error stops the run (`JudgeQuotaStop`); a server
error or a per-minute rate limit is retried after 20, 60 and 120 s, then the
span is left for the next run.

Cost (paid tier): every record has `cost_usd`, from the response's usage
metadata (input, output and thinking tokens; thinking is billed as output) and
`config.GEMINI_PRICING_PER_MTOK`. With a budget the run stops before a span
that would likely cross it, and after `config.JUDGE_REESTIMATE_AFTER` spans it
stops if the measured mean cost projects past the budget for the rest of the
run plus `plan_after` spans of later work.

Calibration against decided spans (`compare`, `classify_decisions`) is in this
module too; see docs/experiments.md, Methodology: blind audio judge.
"""

import difflib
import hashlib
import importlib
import json
import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from talk_autopsy import adjudicate, config, metrics
from talk_autopsy.adjudicate import NO_WORDS, Choice, Consensus, Span

logger = logging.getLogger(__name__)

JUDGE_VERSION = "audio-judge-v1"
MAX_OUTPUT_TOKENS = 8192
RETRY_WAIT_SECONDS = (20, 60, 120)  # after a server error or a per-minute rate limit
PACE_SECONDS = 7.0  # minimum time between request starts (free-tier RPM is not published)
MAX_CONSECUTIVE_FAILED_SPANS = 2  # then the service is treated as down and the run stops
NONE = "none"
EMPTY_OPTION = "(nothing is said in the marked part)"
JUDGED_NOTE = ("Judged by the blind audio judge ({version}, {model}) on {date}: every tick and "
               "`other:` text below is the judge's, not a person's. Spans judged: {done} of {total}.")


class JudgeError(RuntimeError):
    """The judge cannot run on these inputs, or an answer cannot be used."""


class JudgeQuotaStop(JudgeError):
    """The daily quota or the spend cap is used up; the run stopped and can be resumed later."""


def is_spend_cap(error: Exception) -> bool:
    """
    True for a client error that names a spend cap or used-up credits (the AI
    Studio project's monthly spend cap on the paid tier): no retry helps until
    the owner raises the cap or the month turns.
    """
    code = getattr(error, "code", None)
    text = f"{error} {getattr(error, 'message', '') or ''} {getattr(error, 'details', '') or ''}".lower()
    return (code is not None and 400 <= int(code) < 500
            and any(word in text for word in ("spend cap", "spending cap", "spend limit", "spending limit",
                                              "credits are depleted", "prepayment credits")))


def price_of(model: str) -> tuple[float, float]:
    """(input, output) USD per million tokens, or JudgeError: a run is never uncounted."""
    try:
        return config.GEMINI_PRICING_PER_MTOK[model]
    except KeyError:
        raise JudgeError(f"no price for {model} in config.GEMINI_PRICING_PER_MTOK; add it from "
                         f"{config.GEMINI_PRICING_SOURCE} before a paid run") from None


def tokens_of(usage) -> dict:
    """Input, output and thinking tokens from a response's usage metadata (missing counts are 0)."""
    return {"prompt": getattr(usage, "prompt_token_count", 0) or 0,
            "output": getattr(usage, "candidates_token_count", 0) or 0,
            "thinking": getattr(usage, "thoughts_token_count", 0) or 0}


def cost_usd(model: str, tokens: dict) -> float:
    """List-price cost of one response: input at the input price, output and thinking at the output price."""
    price_in, price_out = price_of(model)
    return (tokens["prompt"] * price_in + (tokens["output"] + tokens["thinking"]) * price_out) / 1_000_000


# --- options, seed, prompt ---------------------------------------------------


@dataclass
class ShownOption:
    number: int  # 1..k as shown to the judge
    text: str  # display text; "" for an empty variant
    labels: list[str]  # adjudication-file labels behind this text ("A+B", ...)
    engines: list[str]


def span_seed(fragment: str, number: int) -> int:
    """A stable per-span seed: the same fragment and span always get the same order."""
    digest = hashlib.sha256(f"{JUDGE_VERSION}:{fragment}:{number}".encode()).hexdigest()
    return int(digest[:12], 16)


def _variant_text(option) -> str:
    return "" if option.text == NO_WORDS else option.text


def shown_options(span: Span, seed: int) -> list[ShownOption]:
    """The span's variants, deduplicated by normalized tokens, shuffled, numbered 1..k."""
    groups: dict[tuple[str, ...], ShownOption] = {}
    for option in span.options:
        key = tuple(metrics.tokenize(_variant_text(option)))
        if key in groups:
            groups[key].labels.append(option.label)
            groups[key].engines.extend(option.engines)
        else:
            groups[key] = ShownOption(0, _variant_text(option), [option.label], list(option.engines))
    options = list(groups.values())
    random.Random(seed).shuffle(options)
    for number, option in enumerate(options, start=1):
        option.number = number
    return options


def read_conventions(readme_text: str) -> list[str]:
    """
    The rows of the "Reference conventions" table in data/test-set/README.md as
    `case: rule` lines, without the "*In code:*" notes (those are applied by the
    code, not by the writer).
    """
    match = re.search(r"^## Reference conventions\s*$(.*?)(?=^## |\Z)", readme_text, re.M | re.S)
    if not match:
        raise JudgeError("data/test-set/README.md has no '## Reference conventions' section")
    rules = []
    for line in match.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 2 or cells[0] in ("Case", "") or set(cells[0]) <= set("-: "):
            continue
        rule = re.split(r"\.?\s*\*In code:\*", cells[1])[0].strip().rstrip(".")
        rules.append(f"{cells[0]}: {rule}.")
    if not rules:
        raise JudgeError("the reference conventions table in data/test-set/README.md is empty")
    return rules


PROMPT = """\
You are checking one short passage of an English technical talk against its audio. The speaker \
may have a non-native accent.

Step 1: listen. Transcribe the whole audio clip verbatim into the field `heard`. Write it from the \
audio alone, before you read the options at the end of this message; the options must not change \
what you write in `heard`.

Step 2: find the marked part. It is what is spoken right after {before} and right before {after}. \
These context words come from a machine transcript and may be slightly wrong, and the clip may \
start or end in the middle of a sentence. The marked part can be empty: nothing at all may be \
said between the two context passages.

Step 3: decide. At the end of this message are candidate transcriptions of the marked part from \
automatic transcribers, in random order. Any of them, or all of them, may be wrong. Agreeing with \
the majority, or with any transcriber, is not a goal: only the audio counts. If one option matches \
what is said in the marked part word for word, set `choice` to its number; otherwise set `choice` \
to "none". In `final_text` write the marked part as it should appear in the reference transcript \
(an empty string if nothing is said there). Write only the marked part, not the context words.

Writing rules for `heard` and `final_text`:
- Write every word that is spoken, in the order it is spoken: keep repetitions and false starts; \
do not summarize, shorten or correct the speaker's grammar.
{conventions}

Options for the marked part:
{options}"""


def _quote(words: str, edge: str) -> str:
    return f'"{words}"' if words else f"the {edge} of the clip"


def build_prompt(span: Span, options: list[ShownOption], conventions: list[str]) -> str:
    lines = [f"{o.number}. {o.text or EMPTY_OPTION}" for o in options]
    return PROMPT.format(before=_quote(span.context_before, "start"), after=_quote(span.context_after, "end"),
                         conventions="\n".join(f"- {rule}" for rule in conventions),
                         options="\n".join(lines))


def answer_schema(count: int) -> dict:
    """JSON schema of the answer; property order is the order the model writes them in."""
    return {
        "type": "object",
        "properties": {
            "heard": {"type": "string",
                      "description": "Verbatim transcription of the whole clip, written before the options."},
            "choice": {"type": "string", "enum": [str(n) for n in range(1, count + 1)] + [NONE],
                       "description": "Number of the option that matches the marked part, or none."},
            "final_text": {"type": "string",
                           "description": "The marked part as written; empty if nothing is said."},
        },
        "required": ["heard", "choice", "final_text"],
    }


@dataclass
class Answer:
    heard: str
    choice: str
    final_text: str
    heard_first: bool  # `heard` precedes `choice` in the raw JSON text


def parse_answer(text: str | None, count: int) -> Answer:
    """The model's JSON answer -> Answer. Anything malformed is a JudgeError; nothing is repaired."""
    if not text or not text.strip():
        raise JudgeError("empty answer")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise JudgeError(f"malformed JSON ({e})") from None
    fields = ("heard", "choice", "final_text")
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in fields):
        raise JudgeError("the answer does not have the fields heard, choice, final_text as strings")
    choice = data["choice"].strip().lower()
    if choice != NONE and choice not in {str(n) for n in range(1, count + 1)}:
        raise JudgeError(f"choice {data['choice']!r} is not an option number or none")
    heard_at, choice_at = text.find('"heard"'), text.find('"choice"')
    return Answer(data["heard"], choice, data["final_text"], 0 <= heard_at < choice_at)


def clean_text(text: str) -> str:
    """The conventions applied in code: fillers removed, whitespace collapsed; NO_WORDS -> ''."""
    if text.strip() in (NO_WORDS, EMPTY_OPTION):
        return ""
    return " ".join(word for word in text.split() if not metrics.is_filler(word))


def contains_tokens(haystack: str, needle: str) -> bool:
    """True if the normalized tokens of `needle` occur contiguously in those of `haystack`."""
    hay, sub = metrics.tokenize(haystack), metrics.tokenize(needle)
    if not sub:
        return True
    return any(hay[i:i + len(sub)] == sub for i in range(len(hay) - len(sub) + 1))


# --- the run -------------------------------------------------------------------


@dataclass
class RunSummary:
    total: int  # spans to judge (majority + split)
    done_before: int
    judged_now: int = 0
    failed: list[int] = field(default_factory=list)  # spans left for the next run
    stopped: str = ""  # why the run stopped early, if it did
    spent_usd: float = 0.0  # this run, every response received
    moved_to_pilot: int = 0  # records of another model moved out of judge.jsonl

    @property
    def done(self) -> int:
        return self.done_before + self.judged_now

    @property
    def remaining(self) -> int:
        return self.total - self.done


def load_records(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise JudgeError(f"{path} line {number} is not JSON ({e}); fix or remove that line") from None
    return records


def judgeable(consensus: Consensus) -> list[Span]:
    return [s for s in consensus.listed if s.verdict in ("majority", "split")]


def decision_of(options: list[ShownOption], answer: Answer) -> dict:
    """What goes into the filled file: an option label, or typed text."""
    if answer.choice != NONE:
        option = options[int(answer.choice) - 1]
        return {"label": option.labels[0], "text": option.text, "engines": option.engines}
    text = clean_text(answer.final_text)
    if not text:
        empty = [o for o in options if not o.text]
        if empty:
            return {"label": empty[0].labels[0], "text": "", "engines": empty[0].engines}
    return {"other": text or NO_WORDS, "text": text, "engines": []}


class GeminiCaller:
    """Structured generate_content calls with pacing and retries (the judge, the reference glossary)."""

    def __init__(self, model: str, pace_seconds: float, sleep: Callable[[float], None] = time.sleep,
                 thinking_level: str | None = None):
        from talk_autopsy.stt import gemini

        self.gemini = gemini
        self.model = model
        self.pace_seconds = pace_seconds
        self.sleep = sleep
        self.thinking_level = thinking_level  # None: the model's default
        key = config.require_key("GEMINI_API_KEY")
        genai = importlib.import_module("google.genai")
        self.types = importlib.import_module("google.genai.types")
        self.errors = importlib.import_module("google.genai.errors")
        self.client = genai.Client(api_key=key)
        self.last_start = None
        self.attempts, self.latency = 0, 0.0
        price_of(model)  # an unpriced model fails before the first call
        self.spent_usd = 0.0  # every response received, the rejected ones too (they are billed)

    def _pace(self) -> None:
        if self.last_start is not None:
            wait = self.pace_seconds - (time.monotonic() - self.last_start)
            if wait > 0:
                self.sleep(wait)
        self.last_start = time.monotonic()

    def audio_part(self, audio: bytes):
        return self.types.Part.from_bytes(data=audio, mime_type="audio/wav")

    def generate(self, contents: list, schema: dict):
        """One structured request; `contents` as for generate_content (parts and text)."""
        types, errors, gemini = self.types, self.errors, self.gemini
        settings = {"response_mime_type": "application/json", "response_json_schema": schema,
                    "max_output_tokens": MAX_OUTPUT_TOKENS}
        if self.thinking_level:
            settings["thinking_config"] = types.ThinkingConfig(thinking_level=self.thinking_level)
        for attempt, wait in enumerate((*RETRY_WAIT_SECONDS, None), start=1):
            self._pace()
            self.attempts, started = attempt, time.perf_counter()
            try:
                response = self.client.models.generate_content(
                    model=self.model, contents=contents, config=types.GenerateContentConfig(**settings))
            except errors.APIError as e:
                if is_spend_cap(e):
                    raise JudgeQuotaStop(f"Gemini {self.model}: {gemini._describe(e)} (the project's spend "
                                         "cap is reached)") from e
                if gemini.is_daily_quota_or_billing(e):
                    raise JudgeQuotaStop(f"Gemini {self.model}: {gemini._describe(e)} (daily quota used up "
                                         "or billing required)") from e
                retryable = isinstance(e, errors.ServerError) or gemini._is_quota_error(e, errors)
                if not retryable or wait is None:
                    raise
                logger.warning("Gemini %s: %s (attempt %d); waiting %d s", self.model, gemini._describe(e),
                               attempt, wait)
                self.sleep(wait)
                continue
            self.spent_usd += cost_usd(self.model, tokens_of(getattr(response, "usage_metadata", None)))
            candidates = response.candidates or []
            if not candidates:
                raise JudgeError("no answer (blocked or empty)")
            if candidates[0].finish_reason != types.FinishReason.STOP:
                raise JudgeError(f"stopped with {candidates[0].finish_reason}, not STOP")
            self.latency = time.perf_counter() - started  # the successful call only, no retry waits
            return response
        raise AssertionError("unreachable")


def judge_span(caller: GeminiCaller, fragment: str, span: Span, clip: Path, conventions: list[str]) -> dict:
    """Judge one span; returns its judge.jsonl record. Errors propagate."""
    seed = span_seed(fragment, span.number)
    options = shown_options(span, seed)
    prompt = build_prompt(span, options, conventions)
    response = caller.generate([caller.audio_part(clip.read_bytes()), prompt], answer_schema(len(options)))
    answer = parse_answer(response.text, len(options))
    decision = decision_of(options, answer)
    tokens = tokens_of(response.usage_metadata)
    chosen = decision["text"]
    return {
        "span": span.number, "verdict": span.verdict, "judge": JUDGE_VERSION, "model": caller.model,
        "model_version": getattr(response, "model_version", None), "seed": seed,
        "heard": answer.heard, "heard_first": answer.heard_first,
        "options": [{"n": o.number, "text": o.text, "labels": o.labels, "engines": o.engines}
                    for o in options],
        "choice": answer.choice, "final_text_raw": answer.final_text,
        "final_text": clean_text(answer.final_text), "decision": decision,
        "heard_contains_chosen": contains_tokens(answer.heard, chosen) if chosen else None,
        "latency_seconds": round(caller.latency, 2), "attempts": caller.attempts,
        "thinking_level": caller.thinking_level, "tokens": tokens,
        "cost_usd": round(cost_usd(caller.model, tokens), 6),
        "date": time.strftime("%Y-%m-%d"),
    }


def pilot_path(out: Path) -> Path:
    """Where records of another model go: judge.jsonl -> judge.free-pilot.jsonl."""
    return out.with_name(f"{out.stem}.free-pilot{out.suffix}")


def set_aside_other_models(out: Path, model: str) -> int:
    """
    Move the records of `out` judged by another model to the pilot file (appended),
    keeping only `model`'s records in `out`. Returns how many were moved.
    """
    records = load_records(out)
    other = [r for r in records if r.get("model") != model]
    if not other:
        return 0
    with pilot_path(out).open("a", encoding="utf-8", newline="\n") as pilot:
        for record in other:
            pilot.write(json.dumps(record, ensure_ascii=False) + "\n")
    kept = [r for r in records if r.get("model") == model]
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept), encoding="utf-8",
                   newline="\n")
    return len(other)


def run(fragment: str, consensus: Consensus, clip_dir: Path, conventions: list[str], out: Path,
        model: str, limit: int | None = None, resume: bool = False, pace_seconds: float = PACE_SECONDS,
        progress: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] = time.sleep, thinking_level: str | None = None,
        budget_usd: float | None = None, spent_before_usd: float = 0.0, plan_after: int = 0) -> RunSummary:
    """
    Judge every majority and split span not yet judged by `model` in `out`
    (judge.jsonl). Appends one line per judged span. Records of another model
    are first moved to the pilot file. Raises JudgeQuotaStop only after
    recording what was done; the summary says how many spans remain.

    Budget (optional): `spent_before_usd` is what the session spent before this
    run; the run stops before a span that would likely take the total past
    `budget_usd`, and after `config.JUDGE_REESTIMATE_AFTER` spans if the mean
    cost so far projects past it for the rest of this run plus `plan_after` spans.
    """
    progress = progress or (lambda message: None)
    moved = set_aside_other_models(out, model) if out.is_file() else 0
    if moved:
        progress(f"{moved} records of another model moved to {pilot_path(out).name}; not reused")
    records = load_records(out)
    if records and not resume:
        raise JudgeError(f"{out} exists with {len(records)} judged spans; pass --resume to continue it")
    spans = judgeable(consensus)
    done = {r["span"] for r in records}
    summary = RunSummary(total=len(spans), done_before=len(done & {s.number for s in spans}),
                         moved_to_pilot=moved)
    todo = [s for s in spans if s.number not in done]
    if limit is not None:
        todo = todo[:limit]
    missing = [adjudicate.clip_name(s) for s in todo if not (clip_dir / adjudicate.clip_name(s)).is_file()]
    if missing:
        raise JudgeError(f"{len(missing)} clips missing in {clip_dir} (first: {missing[0]}); "
                         f"rerun talk-autopsy adjudicate {fragment}")
    if not todo:
        return summary
    caller = GeminiCaller(model, pace_seconds, sleep, thinking_level)
    measured = [r["cost_usd"] for r in records if r.get("cost_usd") is not None]
    per_span = sum(measured) / len(measured) if measured else config.JUDGE_ASSUMED_COST_PER_SPAN
    basis = f"measured mean of {len(measured)} spans" if measured else "assumed"
    progress(f"Estimate: {len(todo)} spans x ${per_span:.4f} ({basis}) = ${len(todo) * per_span:.2f}"
             + (f"; {plan_after} more spans planned after this run" if plan_after else "")
             + (f"; budget ${budget_usd:.2f}, spent before ${spent_before_usd:.4f}" if budget_usd is not None
                else ""))
    out.parent.mkdir(parents=True, exist_ok=True)
    consecutive = 0
    with out.open("a", encoding="utf-8", newline="\n") as file:
        for index, span in enumerate(todo):
            stop = _budget_stop(summary, caller, budget_usd, spent_before_usd, per_span,
                                len(todo) - index + plan_after)
            if stop:
                summary.stopped = stop
                break
            clip = clip_dir / adjudicate.clip_name(span)
            try:
                record = judge_span(caller, fragment, span, clip, conventions)
            except JudgeQuotaStop as e:
                summary.stopped = str(e)
                break
            except (JudgeError, caller.errors.APIError) as first:
                logger.warning("span %04d: %s; one more attempt", span.number, type(first).__name__)
                try:
                    record = judge_span(caller, fragment, span, clip, conventions)
                except JudgeQuotaStop as e:
                    summary.stopped = str(e)
                    break
                except (JudgeError, caller.errors.APIError) as second:
                    summary.failed.append(span.number)
                    consecutive += 1
                    progress(f"span {span.number:04d}: failed twice ({_short(second)}); "
                             "left for the next run")
                    if consecutive >= MAX_CONSECUTIVE_FAILED_SPANS:
                        summary.stopped = (f"{consecutive} spans in a row failed; the service "
                                           "looks unavailable, rerun later")
                        break
                    continue
            consecutive = 0
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
            file.flush()
            summary.judged_now += 1
            summary.spent_usd = caller.spent_usd
            progress(f"span {span.number:04d} judged ({summary.done} of {summary.total}); "
                     f"${record['cost_usd']:.4f}, run ${caller.spent_usd:.4f}, "
                     f"session ${spent_before_usd + caller.spent_usd:.4f}")
            if summary.judged_now == config.JUDGE_REESTIMATE_AFTER:
                mean = caller.spent_usd / summary.judged_now
                rest = len(todo) - index - 1 + plan_after
                progress(f"Re-estimate after {summary.judged_now} spans: mean ${mean:.4f} per span; "
                         f"{rest} spans left -> projected session total "
                         f"${spent_before_usd + caller.spent_usd + mean * rest:.2f}")
    summary.spent_usd = caller.spent_usd
    return summary


def _budget_stop(summary: RunSummary, caller: GeminiCaller, budget_usd: float | None, spent_before_usd: float,
                 assumed_per_span: float, spans_left: int) -> str:
    """Why the run must stop before the next span ("" to go on); see `run`."""
    if budget_usd is None:
        return ""
    total = spent_before_usd + caller.spent_usd
    if total >= budget_usd:
        return f"budget reached: ${total:.4f} of ${budget_usd:.2f}"
    mean = caller.spent_usd / summary.judged_now if summary.judged_now else assumed_per_span
    if summary.judged_now >= config.JUDGE_REESTIMATE_AFTER and total + mean * spans_left > budget_usd:
        return (f"projected total ${total + mean * spans_left:.2f} for the {spans_left} spans left "
                f"(mean ${mean:.4f}) exceeds the budget ${budget_usd:.2f}")
    if total + mean > budget_usd:
        return f"the next span (about ${mean:.4f}) would take ${total:.4f} past the budget ${budget_usd:.2f}"
    return ""


def _short(error: Exception) -> str:
    code, status = getattr(error, "code", None), getattr(error, "status", None)
    return " ".join(str(x) for x in (code, status) if x) or type(error).__name__


# --- outputs ---------------------------------------------------------------------


def filled_markdown(adjudication_text: str, records: list[dict], total: int, date: str) -> str:
    """
    A copy of the adjudication file with the judge's decisions: every option
    unticked except the judge's, `other:` holding the judge's text when it chose
    none. Spans without a record are left with nothing ticked, so `resolve`
    refuses the file until the run is complete.
    """
    by_span = {r["span"]: r for r in records}
    models = sorted({r["model"] for r in records}) or ["(none yet)"]
    note = JUDGED_NOTE.format(version=JUDGE_VERSION, model=", ".join(models), date=date,
                              done=len(records), total=total)
    out: list[str] = []
    current: dict = {}
    for index, line in enumerate(adjudication_text.splitlines()):
        header = adjudicate.HEADER.match(line)
        if header:
            current = by_span.get(int(header.group(1)), {}).get("decision", {})
            out.append(line)
            continue
        option = adjudicate.OPTION.match(line.rstrip())
        if option is None:
            out.append(line)
            if index == 0 and line.startswith("# "):
                out += ["", note]
            continue
        label = option.group(2)
        if label == "other":
            out.append(f"- [ ] other: {current.get('other', '')}")
        else:
            mark = "x" if current.get("label") == label else " "
            out.append(f"- [{mark}] {line.rstrip()[len('- [ ] '):]}")
    return "\n".join(out) + "\n"


def run_stats(records: list[dict]) -> dict:
    """Self-preference, none and consistency shares over the judged spans."""
    n = len(records)
    if not n:
        return {"spans": 0}
    gemini = sum(1 for r in records if any(e.startswith("gemini") for e in r["decision"]["engines"]))
    nones = sum(1 for r in records if r["choice"] == NONE)
    checked = [r for r in records if r["heard_contains_chosen"] is not None]
    consistent = sum(1 for r in checked if r["heard_contains_chosen"])
    return {
        "spans": n,
        "gemini_chosen": gemini, "gemini_share": round(gemini / n, 4),
        "none": nones, "none_share": round(nones / n, 4),
        "consistency_checked": len(checked), "consistent": consistent,
        "consistency_share": round(consistent / len(checked), 4) if checked else None,
        "heard_first_share": round(sum(1 for r in records if r["heard_first"]) / n, 4),
        "by_verdict": {v: sum(1 for r in records if r["verdict"] == v) for v in ("majority", "split")},
        "tokens": {k: sum(r["tokens"][k] for r in records) for k in ("prompt", "output", "thinking")},
        "cost_usd": round(sum(r.get("cost_usd") or 0.0 for r in records), 4),
        "latency_seconds_mean": round(sum(r["latency_seconds"] for r in records) / n, 2),
    }


# --- calibration against decided spans -------------------------------------------

_NUMBER_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
                 "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
_PRODUCT_MISHEARD = frozenset({"quadcode", "quad", "cloud", "claudecode"})  # "Claude" as engines misheard it


def _spelling_key(text: str) -> str:
    """Text with case, punctuation, separators and number words ignored (equal keys: spelling only)."""
    words = [_NUMBER_WORDS.get(w, w) for w in metrics.tokenize(text)]
    return re.sub(r"[^0-9a-z]", "", "".join(words))


def decided_text(span: Span, choice: Choice) -> str:
    """The text a filled adjudication file decides for a span (as `resolve` reads it)."""
    if choice.other:
        return "" if choice.other == NO_WORDS else choice.other
    if len(choice.checked) != 1:
        raise JudgeError(f"span {span.number:04d}: {len(choice.checked)} options ticked")
    by_label = {o.label: o for o in span.options}
    return _variant_text(by_label[choice.checked[0]])


def _differs_only_in_terms(decided: list[str], variant: list[str], term_tokens: set[str]) -> bool:
    """True if the decided tokens differ from the variant's, and every differing decided token is a term's."""
    if decided == variant:
        return False
    matcher = difflib.SequenceMatcher(None, variant, decided, autojunk=False)
    changed = [t for tag, _, _, j1, j2 in matcher.get_opcodes() if tag != "equal" for t in decided[j1:j2]]
    return bool(changed) and all(t in term_tokens for t in changed)


def classify_decision(span: Span, choice: Choice, terms: list[str] = ()) -> str:
    """
    How a decision was most likely reached, inferred from its pattern (the owner's
    per-span list was not recorded):

    - `majority`: a majority span decided with the pre-marked text;
    - `rule`: the product-name rule (an option has a mis-hearing of "Claude"
      that the decision replaces with "Claude"); a reference convention (the
      decision differs from another option only in spelling: case, punctuation,
      separators, digits for number words); or the glossary (the decision
      differs from an option only in tokens of the fragment's glossary `terms`);
    - `ear`: everything else (a majority overruled, or a split decided, with
      other words).
    """
    text = decided_text(span, choice)
    tokens = metrics.tokenize(text)
    if span.verdict == "majority" and tokens == metrics.tokenize(_variant_text(span.options[span.premarked])):
        return "majority"
    if "claude" in tokens:
        for option in span.options:
            if _PRODUCT_MISHEARD & set(metrics.tokenize(_variant_text(option))):
                return "rule"
    key = _spelling_key(text)
    term_tokens = {t for term in terms for t in metrics.tokenize(term)}
    for option in span.options:
        variant = _variant_text(option)
        if variant != text and _spelling_key(variant) == key:
            return "rule"
        if _differs_only_in_terms(tokens, metrics.tokenize(variant), term_tokens):
            return "rule"
    return "ear"


def compare(consensus: Consensus, reference: dict[int, Choice], judged: dict[int, Choice],
            terms: list[str] = (), by_ear: frozenset[int] | set[int] = frozenset()) -> dict:
    """
    Agreement of the judge with decided spans: per span, the decided texts are
    compared after the metrics normalization (`metrics.tokenize`), overall and by
    class: `owner_ear` for the spans in `by_ear` (the owner's recorded list of
    spans decided by listening), else `classify_decision` with the fragment's
    glossary `terms`.
    """
    rows = []
    for span in judgeable(consensus):
        if span.number not in reference or span.number not in judged:
            raise JudgeError(f"span {span.number:04d} is missing from one of the files")
        ref_text = decided_text(span, reference[span.number])
        judge_text = decided_text(span, judged[span.number])
        inferred = classify_decision(span, reference[span.number], terms)
        rows.append({"span": span.number, "verdict": span.verdict,
                     "class": "owner_ear" if span.number in by_ear else inferred, "inferred": inferred,
                     "reference": ref_text, "judge": judge_text,
                     "agree": metrics.tokenize(ref_text) == metrics.tokenize(judge_text)})

    def share(selected: list[dict]) -> dict:
        agree = sum(1 for r in selected if r["agree"])
        return {"spans": len(selected), "agree": agree,
                "share": round(agree / len(selected), 4) if selected else None}

    classes = (("owner_ear",) if by_ear else ()) + ("ear", "rule", "majority")
    by_class = {c: share([r for r in rows if r["class"] == c]) for c in classes}
    by_verdict = {v: share([r for r in rows if r["verdict"] == v]) for v in ("majority", "split")}
    return {"overall": share(rows), "by_class": by_class, "by_verdict": by_verdict, "rows": rows}
