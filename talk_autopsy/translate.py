"""
Claude translation of the corrected transcript (prompt versions `translation-v1`,
`translation-v2`; v2 is the default since 2026-11-09: no meaning compression).

    fixed.json + glossary + target_lang -> <target_lang>.json

The transcript goes in blocks of about five minutes (blocks.py) with the
previous block's last segments as read-only context; one glossary serves the
whole video, so a term reads the same in every block. `target_lang` is always
an argument: no language is assumed. The prompt is `prompts/<version>.md`.

Output per block: `{segments: [{id, text}]}`, validated by `validate_block`: the
same ids, no empty segment, no segment left in English (unless every word of it
is a kept glossary term), agreed glossary translations used where the term
occurs. A rejected answer is retried once, then the block fails: its segments
keep their English text and the block is listed in
`meta.translation.failed_blocks`.

A kept term (`keep: yes`) missing from a segment's translation is recorded in
`meta.translation.warnings`, not rejected: compressing a subtitle may drop a
repeated name ("Claude Code" -> "он"), which the prompt asks for. Translation
term accuracy is measured from the output, so the warnings are evidence, not a
gate. (Changed 2026-11-09 after the first T2 run rejected a block for exactly
this; docs/experiments.md, 3.6.)

Returns the same segment ids and times; word timings are dropped (they belong to
the English words).
"""

import logging
import re
import time
from pathlib import Path

from pydantic import BaseModel

from talk_autopsy import blocks, config, metrics
from talk_autopsy.core import claude_client
from talk_autopsy.core.structured_call import InvalidResultError, Usage, request_structured
from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Segment, Transcript, assert_same_ids

logger = logging.getLogger(__name__)

PROMPT_VERSION = "translation-v2"
VERSIONS = ("translation-v1", "translation-v2")
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
ENGLISH_OVERLAP = 0.8  # a segment whose words are this much the source's own words is untranslated
OUTPUT_FACTOR = 3.0  # estimate: output tokens per source token (other script, thinking)
_WORD = re.compile(r"\w+(?:['’]\w+)*")


class SegmentText(BaseModel):
    id: int
    text: str


class TranslationAnswer(BaseModel):
    segments: list[SegmentText]


def load_prompt(version: str = PROMPT_VERSION) -> str:
    return (PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8")


def _words(text: str) -> list[str]:
    return [w.casefold() for w in _WORD.findall(text)]


def _kept_words(glossary: Glossary) -> set[str]:
    return {w for e in glossary.entries if e.keep for w in _words(e.term)}


def left_in_english(source: str, target: str, glossary: Glossary) -> bool:
    """
    True if `target` is mostly the source's own words (ENGLISH_OVERLAP or more of
    its words occur in the source), unless every such word is part of a kept
    glossary term ("Claude Code" may stay "Claude Code").
    """
    target_words = _words(target)
    if not target_words:
        return False
    source_words = set(_words(source))
    shared = [w for w in target_words if w in source_words]
    if len(shared) / len(target_words) < ENGLISH_OVERLAP:
        return False
    return not set(shared) <= _kept_words(glossary)


def _stem_present(translation: str, target: str) -> bool:
    """Every word of an agreed translation occurs in `target`, allowing for inflected endings."""
    words = _words(target)
    for part in _words(translation):
        stem = part[:max(4, len(part) - 2)]
        if not any(w.startswith(stem) for w in words):
            return False
    return True


def kept_terms_missing(segments: list[Segment], glossary: Glossary, answer: TranslationAnswer) -> list[str]:
    """Warnings: kept glossary terms that occur in a source segment but not in its translation."""
    warnings = []
    for source, out in zip(segments, answer.segments, strict=False):
        for entry in glossary.entries:
            if not entry.keep or not metrics.term_accuracy(source.text, source.text, [entry.term]).rows:
                continue
            if entry.term.casefold() not in out.text.casefold():
                warnings.append(f"segment {out.id}: kept term {entry.term!r} is missing")
    return warnings


def validate_block(segments: list[Segment], glossary: Glossary, target_lang: str,
                   answer: TranslationAnswer) -> list[str]:
    """Problems with a translation answer for `segments`; empty if it can be used."""
    want = [s.id for s in segments]
    got = [s.id for s in answer.segments]
    if got != want:
        return [f"segment ids {got} differ from the input {want}"]
    problems = []
    for source, out in zip(segments, answer.segments, strict=True):
        if not out.text.strip():
            problems.append(f"segment {out.id} is empty")
            continue
        if left_in_english(source.text, out.text, glossary):
            problems.append(f"segment {out.id} is left in English: {out.text[:60]!r}")
        for entry in glossary.entries:
            occurs = metrics.term_accuracy(source.text, source.text, [entry.term]).rows
            if not occurs:
                continue
            agreed = entry.translations.get(target_lang)
            if not entry.keep and agreed and not _stem_present(agreed, out.text):
                problems.append(f"segment {out.id}: glossary translation {agreed!r} of {entry.term!r} "
                                "not used")
    return problems


def _prefix(prompt: str, glossary: Glossary, context: blocks.VideoContext, target_lang: str) -> str:
    return (f"{prompt}\n\n## Target language\n\n{target_lang} (ISO 639 code)\n\n"
            f"## Glossary\n\n{blocks.glossary_text(glossary, target_lang)}\n\n"
            f"## Video context\n\n{context.text()}\n")


def _user(block: blocks.Block) -> str:
    parts = []
    if block.context:
        parts.append("Context segments (read-only, do not return):\n" + blocks.numbered(block.context))
    parts.append("Segments to translate:\n" + blocks.numbered(block.segments))
    return "\n\n".join(parts)


def estimate(transcript: Transcript, glossary: Glossary, target_lang: str,
             context: blocks.VideoContext | None = None, model: str | None = None,
             version: str = PROMPT_VERSION) -> blocks.Estimate:
    model = model or config.TRANSLATION_MODEL
    target_lang = config.check_lang(target_lang)
    prefix_tokens = blocks.estimate_tokens(_prefix(load_prompt(version), glossary,
                                                   context or blocks.VideoContext(), target_lang))
    parts = blocks.make_blocks(transcript)
    input_tokens = output_tokens = 0
    for block in parts:
        input_tokens += prefix_tokens + blocks.estimate_tokens(_user(block))
        output_tokens += round(OUTPUT_FACTOR * blocks.estimate_tokens(blocks.numbered(block.segments)))
    return blocks.Estimate(len(parts), input_tokens, output_tokens,
                           blocks.price(model, input_tokens, output_tokens))


def translate(transcript: Transcript, glossary: Glossary, target_lang: str, *, model: str | None = None,
              context: blocks.VideoContext | None = None, version: str = PROMPT_VERSION,
              progress=None) -> Transcript:
    """The translated transcript: same ids and times, texts in `target_lang`, usage in `meta.translation`."""
    config.require_api_key()
    target_lang = config.check_lang(target_lang)
    model = model or config.TRANSLATION_MODEL
    prefix = _prefix(load_prompt(version), glossary, context or blocks.VideoContext(), target_lang)
    client = claude_client.get_client()
    texts: dict[int, str] = {}
    failed: list[dict] = []
    warnings: list[str] = []
    usage = Usage()
    started = time.perf_counter()
    parts = blocks.make_blocks(transcript)
    for block in parts:
        label = f"{version} block {block.number}/{len(parts)}"
        try:
            response = request_structured(
                client, label=label, model=model, max_tokens=config.MAX_TOKENS, system="",
                user=_user(block), output_format=TranslationAnswer, cached_prefix=prefix,
                validate=lambda answer, segments=block.segments: validate_block(
                    segments, glossary, target_lang, answer),
            )
        except InvalidResultError as e:
            blocks.merge_usage(usage, e.usage)
            failed.append({"block": block.number, "ids": [s.id for s in block.segments], "error": str(e)})
            logger.warning("%s failed validation twice; its segments stay in English: %s", label, e)
        else:
            blocks.merge_usage(usage, response.usage)
            texts.update({s.id: s.text for s in response.result.segments})
            warnings.extend(kept_terms_missing(block.segments, glossary, response.result))
        if progress is not None:
            progress(block.number, len(parts))

    segments = [Segment(id=s.id, start=s.start, end=s.end, text=texts.get(s.id, s.text), words=None,
                        confidence=None) for s in transcript.segments]
    meta = blocks.stage_meta(transcript.meta, blocks.usage_cost(model, usage), time.perf_counter() - started)
    meta["translation"] = {
        "prompt_version": version,
        "model": model,
        "target_lang": target_lang,
        "blocks": len(parts),
        "failed_blocks": failed,
        "warnings": warnings,
        "usage": usage.as_dict(),
        "cost_usd": blocks.usage_cost(model, usage),
        "input_source": transcript.source,
    }
    result = Transcript(segments=segments, language=target_lang,
                        source=f"{transcript.source} + {version}", meta=meta)
    assert_same_ids(transcript, result)
    return result
