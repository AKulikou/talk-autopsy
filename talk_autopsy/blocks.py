"""
What the Claude stages (correct.py, translate.py) share: blocks of segments,
the video context, token and cost estimates, and the run record.

A block is about BLOCK_SECONDS of consecutive segments. The last
CONTEXT_SEGMENTS segments of the previous block go along as read-only context,
so the model knows how the block starts; they are never returned.
"""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from talk_autopsy import config
from talk_autopsy.core import token_summary
from talk_autopsy.core.structured_call import Usage
from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Segment, Transcript

BLOCK_SECONDS = 300.0
CONTEXT_SEGMENTS = 3


@dataclass
class Block:
    number: int
    context: list[Segment]  # read-only, from the previous block
    segments: list[Segment]


def make_blocks(transcript: Transcript, block_seconds: float = BLOCK_SECONDS,
                context_segments: int = CONTEXT_SEGMENTS) -> list[Block]:
    """Consecutive segments in blocks of about `block_seconds`; a block holds at least one segment."""
    blocks: list[Block] = []
    current: list[Segment] = []
    for segment in transcript.segments:
        if current and segment.start - current[0].start >= block_seconds:
            blocks.append(Block(len(blocks) + 1, [], current))
            current = []
        current.append(segment)
    if current:
        blocks.append(Block(len(blocks) + 1, [], current))
    for previous, block in zip(blocks, blocks[1:], strict=False):
        block.context = previous.segments[-context_segments:] if context_segments else []
    return blocks


def numbered(segments: list[Segment]) -> str:
    return "\n".join(f"[{s.id}] {s.text}" for s in segments)


# --- video context -------------------------------------------------------------


@dataclass
class VideoContext:
    """What is known about the video: from meta.json (title, description, chapters)."""

    title: str = ""
    description: str = ""
    chapters: list[str] = field(default_factory=list)

    def text(self) -> str:
        parts = [f"Title: {self.title or '(unknown)'}"]
        if self.description:
            parts.append(f"Description:\n{self.description.strip()}")
        if self.chapters:
            parts.append("Chapters:\n" + "\n".join(f"- {c}" for c in self.chapters))
        return "\n".join(parts)


def load_context(path: Path | None) -> VideoContext:
    """meta.json -> VideoContext; a missing file gives an empty context (local files have none)."""
    if path is None or not Path(path).is_file():
        return VideoContext()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    chapters = [c.get("title", "") if isinstance(c, dict) else str(c) for c in data.get("chapters") or []]
    return VideoContext(title=data.get("title") or "", description=data.get("description") or "",
                        chapters=[c for c in chapters if c])


def glossary_text(glossary: Glossary, target_lang: str | None = None) -> str:
    """The glossary as lines for a prompt; with `target_lang`, the agreed translation or 'keep'."""
    lines = []
    for e in glossary.entries:
        line = f"- {e.term}"
        if target_lang is not None:
            if e.keep:
                line += " (keep as is, do not translate)"
            elif e.translations.get(target_lang):
                line += f" -> {e.translations[target_lang]}"
        if e.notes:
            line += f" ({e.notes})"
        lines.append(line)
    return "\n".join(lines) if lines else "(no glossary)"


# --- tokens and cost -------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """
    Rough token count: about 3.5 characters per token for Latin script and 2
    for other scripts (Cyrillic and others tokenize into more pieces). Used only
    for the estimate printed before a run; the real counts come from `usage`.
    """
    latin = sum(1 for ch in text if ord(ch) < 0x250)
    return math.ceil(latin / 3.5 + (len(text) - latin) / 2.0)


def price(model: str, input_tokens: int, output_tokens: int) -> float:
    cost = token_summary.estimate_cost(input_tokens, output_tokens, model)
    if cost is None:
        raise config.ConfigurationError(f"no price for {model} in config.MODEL_PRICING_PER_MTOK")
    return cost


def usage_cost(model: str, usage: Usage) -> float:
    cost = token_summary.estimate_cost(usage.input_tokens, usage.output_tokens, model,
                                       usage.cache_creation_input_tokens, usage.cache_read_input_tokens)
    return round(cost or 0.0, 6)


@dataclass(frozen=True)
class Estimate:
    blocks: int
    input_tokens: int
    output_tokens: int
    usd: float

    def line(self) -> str:
        return (f"{self.blocks} block(s), about {self.input_tokens:,} input and {self.output_tokens:,} "
                f"output tokens, about {self.usd:.3f} USD")


def merge_usage(total: Usage, part: Usage) -> None:
    total.input_tokens += part.input_tokens
    total.output_tokens += part.output_tokens
    total.cache_creation_input_tokens += part.cache_creation_input_tokens
    total.cache_read_input_tokens += part.cache_read_input_tokens
    total.web_searches += part.web_searches


def stage_meta(input_meta: dict, cost_usd: float, wall_seconds: float) -> dict:
    """
    The input's meta, carried forward, with this stage's own cost and wall time
    in `cost_usd` and `wall_seconds` (what a benchmark row records); the input's
    values move to `input_cost_usd` and `input_wall_seconds`.
    """
    meta = dict(input_meta)
    meta["input_cost_usd"] = input_meta.get("cost_usd")
    meta["input_wall_seconds"] = input_meta.get("wall_seconds")
    meta["cost_usd"] = round(cost_usd, 6)
    meta["wall_seconds"] = round(wall_seconds, 1)
    return meta


def best_revert(answers: list, revert) -> tuple | None:
    """Of the rejected answers, the usable revert with the fewest reverted segments (the later on a tie)."""
    best = None
    for answer in answers:
        found = revert(answer)
        if found is not None and (best is None or len(found[1]) <= len(best[1])):
            best = found
    return best
