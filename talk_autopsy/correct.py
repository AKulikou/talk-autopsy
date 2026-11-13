"""
Claude correction of the raw transcript (prompt versions `correction-v1`,
`correction-v2`; v2 is the default since 2026-11-09).

    raw or aligned Transcript + glossary + video context -> fixed.json

Fixes technical terms, names and words distorted by the speaker's accent; leaves
everything else alone (the false-edit rate is measured). The transcript goes in
blocks of about five minutes (blocks.py), each with the previous block's last
three segments as read-only context. The prompt is a file,
`prompts/correction-<version>.md`, so prompt versions are diffable.

Output per block: `{segments: [{id, text}], edits: [{id, before, after, reason}]}`.
A schema-valid answer can still be garbage, so `validate_block` checks it; a
rejected answer is retried once (core/structured_call.py). When both attempts
are rejected, the problems decide (pipeline-v3.4, 2026-11-13):

- only problems of single segments (empty, changed without an edit, words
  borrowed from a neighbour, a glossary term removed in it): the block is
  accepted with just those segments reverted to their input text, provided the
  result then passes every check (`revert_segments`; of the two answers, the one
  that reverts fewer segments, else the second). They are listed in
  `meta.correction.reverted_segments` with their problems;
- a structural problem (other segment ids, an edit for a segment not in the
  block, a removed term no segment accounts for), or a block that still fails
  after the revert: the block fails as before; its segments keep their input
  text and it is listed in `meta.correction.failed_blocks`.

Either way nothing is invented and the failure is visible. Before v3.4 one bad
segment failed the whole block (five minutes of a real talk on "glossary terms
removed: MCP").

A glossary term written as a command or code name ("run MCP" -> "run /mcp",
the speaker naming the `/mcp` command) is not a removal (pipeline-v3.4).

Two checks keep every edit inside its segment (added with correction-v2,
after v1 rebuilt a garbled audience question from the next segment's words;
they apply to every version):

- a segment's output keeps at least MIN_KEPT_SHARE of its input words in order
  (difflib on metrics-normalized tokens), unless every changed run is covered
  by an edit listed for the segment (its `before` and `after` words);
- no output segment holds a run of MOVED_RUN or more words that occurs in a
  neighbouring input segment (the previous one, or the last read-only context
  segment, and the next one) but not in its own input. To spare single-word
  fixes that happen to match a neighbour ("sink" -> "think" next to "I think
  so"), the run counts only when at least two of its words are new to the
  segment and are not glossary-term words.

Returns the same segment ids (segments.assert_same_ids). A segment whose text
changed loses its word timings (the words no longer match the text); its
start and end stay.
"""

import difflib
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from talk_autopsy import blocks, config, metrics
from talk_autopsy.core import claude_client
from talk_autopsy.core.structured_call import InvalidResultError, Usage, request_structured, tell_observer
from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Segment, Transcript, assert_same_ids

logger = logging.getLogger(__name__)

PROMPT_VERSION = "correction-v2"
VERSIONS = ("correction-v1", "correction-v2")
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
MAX_CHANGED_SHARE = 0.40  # a segment changed more than this needs an edit with a reason
MAX_LENGTH_DRIFT = 0.10  # the block's text length must stay within +-10%
MIN_KEPT_SHARE = 0.70  # share of a segment's input words its output keeps in order
MOVED_RUN = 3  # words in a row taken from a neighbouring segment
OUTPUT_FACTOR = 2.5  # estimate: output tokens per input-transcript token (echo, edits, thinking)


class SegmentText(BaseModel):
    id: int
    text: str


class Edit(BaseModel):
    id: int
    before: str
    after: str
    reason: str


class CorrectionAnswer(BaseModel):
    segments: list[SegmentText]
    edits: list[Edit]


def load_prompt(version: str = PROMPT_VERSION) -> str:
    return (PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8")


def changed_share(before: str, after: str) -> float:
    """Share of characters changed: 1 - difflib's similarity ratio (0 = identical, 1 = nothing shared)."""
    if before == after:
        return 0.0
    return 1.0 - difflib.SequenceMatcher(None, before, after, autojunk=False).ratio()


def kept_share(before: str, after: str) -> float:
    """Share of `before`'s normalized words that `after` keeps in the same order (1.0 for an empty input)."""
    a, b = metrics.tokenize(before), metrics.tokenize(after)
    if not a:
        return 1.0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(m.size for m in matcher.get_matching_blocks()) / len(a)


def changes_covered(before: str, after: str, edits: list[Edit]) -> bool:
    """True if every changed run between `before` and `after` is made of words of the listed edits."""
    if not edits:
        return False
    a, b = metrics.tokenize(before), metrics.tokenize(after)
    edit_before = {w for e in edits for w in metrics.tokenize(e.before)}
    edit_after = {w for e in edits for w in metrics.tokenize(e.after)}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal" and not (set(a[i1:i2]) <= edit_before and set(b[j1:j2]) <= edit_after):
            return False
    return True


def _ngrams(tokens: list[str], n: int = MOVED_RUN) -> set[tuple[str, ...]]:
    return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def moved_runs(own_input: str, output: str, neighbours: list[str], glossary: Glossary) -> list[str]:
    """Runs of MOVED_RUN words in `output` that come from a neighbouring input segment, not from its own."""
    own = metrics.tokenize(own_input)
    own_words, own_runs = set(own), _ngrams(own)
    term_words = {w for e in glossary.entries for w in metrics.tokenize(e.term)}
    out = metrics.tokenize(output)
    found: list[str] = []
    for neighbour in neighbours:
        theirs = _ngrams(metrics.tokenize(neighbour))
        for run in _ngrams(out):
            if run in own_runs or run not in theirs:
                continue
            new = [w for w in run if w not in own_words and w not in term_words]
            if len(new) >= 2 and " ".join(run) not in found:
                found.append(" ".join(run))
    return found


def merged_into_term(term: str, terms: list[str], edits: list[Edit]) -> bool:
    """
    True if a listed edit turned words holding `term` into another glossary term:
    an auto-glossary can list both `MCP` and `.mcp.json` (heard as "MCP JSON"),
    and the right fix "MCP JSON" -> ".mcp.json" removes the term `MCP`.
    """
    for e in edits:
        if not metrics.term_accuracy(e.before, e.before, [term]).rows:
            continue
        for other in terms:
            if other == term:
                continue
            gained = metrics.term_accuracy(e.after, e.after, [other]).rows
            had = metrics.term_accuracy(e.before, e.before, [other]).rows
            if gained and (not had or gained[0].expected > had[0].expected):
                return True
    return False


def command_forms(term: str, text: str) -> int:
    """
    How often `text` writes `term` as a command or code name: "/mcp", "`/mcp`",
    "@mcp" (case aside). The term counter sees "/mcp" as another word.
    """
    if not re.fullmatch(r"[\w.+-]+", term):
        return 0
    pattern = rf"(?<![\w/.\-])[/@]{re.escape(term)}(?![\w\-])"
    return len(re.findall(pattern, text, flags=re.IGNORECASE))


def _removed_terms(before: str, after: str, terms: list[str], edits: list[Edit]) -> list[str]:
    """Glossary terms `after` holds fewer times than `before`, not explained by a merge or a command form."""
    removed = []
    for row in metrics.term_accuracy(before, after, terms).rows:
        if row.found >= row.expected or merged_into_term(row.term, terms, edits):
            continue
        if command_forms(row.term, after) - command_forms(row.term, before) >= row.expected - row.found:
            continue  # "run MCP" -> "run /mcp" (pipeline-v3.4)
        removed.append(row.term)
    return removed


@dataclass
class BlockCheck:
    """
    The problems of a correction answer: all of them in the order validate_block
    reports them, and which of them belong to one segment or to the whole block.
    """

    problems: list[str] = field(default_factory=list)
    structural: list[str] = field(default_factory=list)  # only the whole block can answer for these
    segments: dict[int, list[str]] = field(default_factory=dict)  # segment id -> its own problems

    def add(self, problem: str, segment_id: int | None = None, structural: bool = False) -> None:
        self.problems.append(problem)
        if structural:
            self.structural.append(problem)
        elif segment_id is not None:
            self.segments.setdefault(segment_id, []).append(problem)


def check_block(segments: list[Segment], glossary: Glossary, answer: CorrectionAnswer,
                context: list[Segment] | None = None) -> BlockCheck:
    """
    validate_block's problems, sorted: structural, per segment, and the block's
    text length (neither: a revert may cure it, so a reverted answer is checked
    again in full).
    """
    check = BlockCheck()
    want = [s.id for s in segments]
    got = [s.id for s in answer.segments]
    if got != want:
        check.add(f"segment ids {got} differ from the input {want}", structural=True)
        return check
    by_id = {s.id: s.text for s in segments}
    explained = {e.id for e in answer.edits if e.reason.strip()}
    for e in answer.edits:
        if e.id not in by_id:
            check.add(f"edit for segment {e.id}, which is not in the block", structural=True)
    for s in answer.segments:
        if not s.text.strip():
            check.add(f"segment {s.id} is empty", s.id)
            continue
        share = changed_share(by_id[s.id], s.text)
        if share > MAX_CHANGED_SHARE and s.id not in explained:
            check.add(f"segment {s.id} changed {share:.0%} of its characters without an edit", s.id)
        kept = kept_share(by_id[s.id], s.text)
        if kept < MIN_KEPT_SHARE and not changes_covered(by_id[s.id], s.text,
                                                         [e for e in answer.edits if e.id == s.id]):
            check.add(f"segment {s.id} keeps only {kept:.0%} of its words in order, and not every "
                      "change is listed in an edit", s.id)
    inputs = [*(context or [])[-1:], *segments]
    offset = len(inputs) - len(segments)
    for k, s in enumerate(answer.segments):
        position = k + offset
        neighbours = [inputs[i].text for i in (position - 1, position + 1) if 0 <= i < len(inputs)]
        runs = moved_runs(by_id[s.id], s.text, neighbours, glossary)
        if runs:
            check.add(f"segment {s.id} holds words of a neighbouring segment: {'; '.join(runs)}", s.id)
    before = " ".join(s.text for s in segments)
    after = " ".join(s.text for s in answer.segments)
    terms = [e.term for e in glossary.entries]
    if terms:
        removed = _removed_terms(before, after, terms, answer.edits)
        if removed:
            check.problems.append(f"glossary terms removed: {', '.join(removed)}")
            for term in removed:  # the segments that lost it; none (the term spanned two): structural
                losing = [out.id for source, out in zip(segments, answer.segments, strict=True)
                          if _removed_terms(source.text, out.text, [term],
                                            [e for e in answer.edits if e.id == out.id])]
                for segment_id in losing:
                    check.segments.setdefault(segment_id, []).append(f"glossary terms removed: {term}")
                if not losing:
                    check.structural.append(f"glossary terms removed: {term}")
    if before and abs(len(after) - len(before)) > MAX_LENGTH_DRIFT * len(before):
        check.problems.append(f"text length changed from {len(before)} to {len(after)} characters (over 10%)")
    return check


def validate_block(segments: list[Segment], glossary: Glossary, answer: CorrectionAnswer,
                   context: list[Segment] | None = None) -> list[str]:
    """
    Problems with a correction answer for `segments` (`context`: the read-only
    segments before them); empty if it can be used.
    """
    return check_block(segments, glossary, answer, context).problems


def revert_segments(segments: list[Segment], glossary: Glossary, answer: CorrectionAnswer,
                    context: list[Segment] | None = None
                    ) -> tuple[CorrectionAnswer, dict[int, list[str]]] | None:
    """
    A rejected answer with only per-segment problems, made usable: those segments
    get their input text back and lose their edits. Returns the answer and the
    reverted segments' problems, or None when a structural problem remains or the
    reverted answer still fails a check.
    """
    check = check_block(segments, glossary, answer, context)
    if check.structural or not check.segments or len(check.segments) == len(answer.segments):
        return None  # a structural problem, nothing wrong, or every segment wrong: the block fails
    reverted = check.segments
    by_id = {s.id: s.text for s in segments}
    repaired = CorrectionAnswer(
        segments=[SegmentText(id=s.id, text=by_id[s.id] if s.id in reverted else s.text)
                  for s in answer.segments],
        edits=[e for e in answer.edits if e.id not in reverted])
    if validate_block(segments, glossary, repaired, context):
        return None
    return repaired, reverted


def _prefix(prompt: str, glossary: Glossary, context: blocks.VideoContext) -> str:
    """Rules, glossary and video context: the same for every block of a video, so it is cached."""
    return (f"{prompt}\n\n## Glossary\n\n{blocks.glossary_text(glossary)}\n\n"
            f"## Video context\n\n{context.text()}\n")


def _user(block: blocks.Block) -> str:
    parts = []
    if block.context:
        parts.append("Context segments (read-only, do not return):\n" + blocks.numbered(block.context))
    parts.append("Segments to correct:\n" + blocks.numbered(block.segments))
    return "\n\n".join(parts)


def estimate(transcript: Transcript, glossary: Glossary, context: blocks.VideoContext,
             model: str | None = None, version: str = PROMPT_VERSION) -> blocks.Estimate:
    """Cost estimate before a run, from character counts and config prices (no API call)."""
    model = model or config.correction_model()
    prefix_tokens = blocks.estimate_tokens(_prefix(load_prompt(version), glossary, context))
    parts = blocks.make_blocks(transcript)
    input_tokens = output_tokens = 0
    for block in parts:
        text_tokens = blocks.estimate_tokens(_user(block))
        input_tokens += prefix_tokens + text_tokens
        output_tokens += round(OUTPUT_FACTOR * blocks.estimate_tokens(blocks.numbered(block.segments)))
    return blocks.Estimate(len(parts), input_tokens, output_tokens,
                           blocks.price(model, input_tokens, output_tokens))


def correct(transcript: Transcript, glossary: Glossary, context: blocks.VideoContext | str, *,
            model: str | None = None, version: str = PROMPT_VERSION, progress=None,
            refusal_fallback: bool = False, done: dict[int, str] | None = None,
            done_edits: list[dict] | None = None) -> Transcript:
    """
    The corrected transcript: same ids and times, corrected texts, the edits and
    the API usage in `meta.correction`. `progress(block_number, block_count)` is
    called after each block.

    `done`: segment id -> text an earlier run accepted for a block whose request
    would be the same (pipeline-v3.4); a block whose every segment is in it is not
    sent (`meta.correction.reused_blocks`), and its edits come from `done_edits`.
    """
    config.require_api_key()
    if isinstance(context, str):
        context = blocks.VideoContext(title=context)
    model = model or config.correction_model()
    prompt = load_prompt(version)
    prefix = _prefix(prompt, glossary, context)
    client = claude_client.get_client()
    texts: dict[int, str] = {}
    edits: list[dict] = []
    failed: list[dict] = []
    usage = Usage()
    served_by: set[str] = set()
    started = time.perf_counter()
    parts = blocks.make_blocks(transcript)
    reverted: list[dict] = []
    reused: list[int] = []
    for block in parts:
        label = f"{version} block {block.number}/{len(parts)}"
        if done and all(s.id in done for s in block.segments):
            ids = {s.id for s in block.segments}
            texts.update({s.id: done[s.id] for s in block.segments})
            edits.extend(e for e in done_edits or [] if e.get("id") in ids)
            reused.append(block.number)
            if progress is not None:
                progress(block.number, len(parts))
            continue
        try:
            response = request_structured(
                client, label=label, model=model, max_tokens=config.MAX_TOKENS, system="",
                user=_user(block), output_format=CorrectionAnswer, cached_prefix=prefix,
                refusal_fallback=refusal_fallback,
                validate=lambda answer, segments=block.segments, context=block.context: validate_block(
                    segments, glossary, answer, context),
            )
        except InvalidResultError as e:
            blocks.merge_usage(usage, e.usage)
            usable = blocks.best_revert(e.rejected, lambda answer, block=block: revert_segments(
                block.segments, glossary, answer, block.context))
            if usable is None:
                failed.append({"block": block.number, "ids": [s.id for s in block.segments], "error": str(e)})
                logger.warning("%s failed validation twice; its segments keep their input text: %s", label, e)
            else:
                answer, problems = usable
                reverted.append({"block": block.number, "ids": sorted(problems),
                                 "problems": {str(i): problems[i] for i in sorted(problems)}})
                logger.warning("%s: both answers were rejected for single segments only; %d segment(s) keep "
                               "their input text, the block's other corrections are kept: %s", label,
                               len(problems), "; ".join(p for i in sorted(problems) for p in problems[i]))
                texts.update({s.id: s.text for s in answer.segments})
                edits.extend(e.model_dump() for e in answer.edits)
                tell_observer(label, answer, reverted=sorted(problems))
        else:
            blocks.merge_usage(usage, response.usage)
            served_by.add(response.model or model)
            texts.update({s.id: s.text for s in response.result.segments})
            edits.extend(e.model_dump() for e in response.result.edits)
        if progress is not None:
            progress(block.number, len(parts))

    segments = []
    for s in transcript.segments:
        text = texts.get(s.id, s.text)
        changed = text != s.text
        segments.append(Segment(id=s.id, start=s.start, end=s.end, text=text,
                                words=None if changed else s.words, confidence=s.confidence))
    meta = blocks.stage_meta(transcript.meta, blocks.usage_cost(model, usage), time.perf_counter() - started)
    meta["correction"] = {
        "prompt_version": version,
        "model": model,
        "served_by": sorted(served_by),  # differs from model only after a refusal fallback
        "refusal_fallback": refusal_fallback,
        "blocks": len(parts),
        "failed_blocks": failed,
        **({"reverted_segments": reverted} if reverted else {}),
        **({"reused_blocks": reused} if done else {}),
        "segments_changed": sum(1 for a, b in zip(transcript.segments, segments, strict=True)
                                if a.text != b.text),
        "edits": edits,
        "usage": usage.as_dict(),
        "cost_usd": blocks.usage_cost(model, usage),
        "input_source": transcript.source,
    }
    result = Transcript(segments=segments, language=transcript.language,
                        source=f"{transcript.source} + {version}", meta=meta)
    assert_same_ids(transcript, result)
    return result
