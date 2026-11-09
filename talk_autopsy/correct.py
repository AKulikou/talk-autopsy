"""
Claude correction of the raw transcript (prompt version `correction-v1`).

    raw or aligned Transcript + glossary + video context -> fixed.json

Fixes technical terms, names and words distorted by the speaker's accent; leaves
everything else alone (the false-edit rate is measured). The transcript goes in
blocks of about five minutes (blocks.py), each with the previous block's last
three segments as read-only context. The prompt is a file,
`prompts/correction-v1.md`, so prompt versions are diffable.

Output per block: `{segments: [{id, text}], edits: [{id, before, after, reason}]}`.
A schema-valid answer can still be garbage, so `validate_block` checks it; a
rejected answer is retried once (core/structured_call.py), then the block fails:
its segments keep their input text and the block is listed in
`meta.correction.failed_blocks` (nothing is invented, and the failure is visible).

Returns the same segment ids (segments.assert_same_ids). A segment whose text
changed loses its word timings (the words no longer match the text); its
start and end stay.
"""

import difflib
import logging
import time
from pathlib import Path

from pydantic import BaseModel

from talk_autopsy import blocks, config, metrics
from talk_autopsy.core import claude_client
from talk_autopsy.core.structured_call import InvalidResultError, Usage, request_structured
from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Segment, Transcript, assert_same_ids

logger = logging.getLogger(__name__)

PROMPT_VERSION = "correction-v1"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
MAX_CHANGED_SHARE = 0.40  # a segment changed more than this needs an edit with a reason
MAX_LENGTH_DRIFT = 0.10  # the block's text length must stay within +-10%
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


def validate_block(segments: list[Segment], glossary: Glossary, answer: CorrectionAnswer) -> list[str]:
    """Problems with a correction answer for `segments`; empty if it can be used."""
    problems = []
    want = [s.id for s in segments]
    got = [s.id for s in answer.segments]
    if got != want:
        return [f"segment ids {got} differ from the input {want}"]
    by_id = {s.id: s.text for s in segments}
    explained = {e.id for e in answer.edits if e.reason.strip()}
    for e in answer.edits:
        if e.id not in by_id:
            problems.append(f"edit for segment {e.id}, which is not in the block")
    for s in answer.segments:
        if not s.text.strip():
            problems.append(f"segment {s.id} is empty")
            continue
        share = changed_share(by_id[s.id], s.text)
        if share > MAX_CHANGED_SHARE and s.id not in explained:
            problems.append(f"segment {s.id} changed {share:.0%} of its characters without an edit")
    before = " ".join(s.text for s in segments)
    after = " ".join(s.text for s in answer.segments)
    terms = [e.term for e in glossary.entries]
    if terms:
        removed = [r.term for r in metrics.term_accuracy(before, after, terms).rows if r.found < r.expected]
        if removed:
            problems.append(f"glossary terms removed: {', '.join(removed)}")
    if before and abs(len(after) - len(before)) > MAX_LENGTH_DRIFT * len(before):
        problems.append(f"text length changed from {len(before)} to {len(after)} characters (over 10%)")
    return problems


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
    model = model or config.CORRECTION_MODEL
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
            model: str | None = None, version: str = PROMPT_VERSION, progress=None) -> Transcript:
    """
    The corrected transcript: same ids and times, corrected texts, the edits and
    the API usage in `meta.correction`. `progress(block_number, block_count)` is
    called after each block.
    """
    config.require_api_key()
    if isinstance(context, str):
        context = blocks.VideoContext(title=context)
    model = model or config.CORRECTION_MODEL
    prompt = load_prompt(version)
    prefix = _prefix(prompt, glossary, context)
    client = claude_client.get_client()
    texts: dict[int, str] = {}
    edits: list[dict] = []
    failed: list[dict] = []
    usage = Usage()
    started = time.perf_counter()
    parts = blocks.make_blocks(transcript)
    for block in parts:
        label = f"{version} block {block.number}/{len(parts)}"
        try:
            response = request_structured(
                client, label=label, model=model, max_tokens=config.MAX_TOKENS, system="",
                user=_user(block), output_format=CorrectionAnswer, cached_prefix=prefix,
                validate=lambda answer, segments=block.segments: validate_block(segments, glossary, answer),
            )
        except InvalidResultError as e:
            blocks.merge_usage(usage, e.usage)
            failed.append({"block": block.number, "ids": [s.id for s in block.segments], "error": str(e)})
            logger.warning("%s failed validation twice; its segments keep their input text: %s", label, e)
        else:
            blocks.merge_usage(usage, response.usage)
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
        "blocks": len(parts),
        "failed_blocks": failed,
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
