"""
Claude translation of the corrected transcript (prompt versions `translation-v1`,
`translation-v2`, `translation-v2.1`; v2 is the default since 2026-11-09: no meaning
compression; v2.1, the default since 2026-11-11, is v2 with its examples replaced by
examples from outside the test set).

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

**translation-v3** (an option since 2026-11-10; the default stays v2 until the
owner's blind rating): the glossary carries a policy per term (keep / both /
translate / ui, glossary.py; terms classified by `term_policy.py`). The model
marks `both` and `ui` terms inline (`⟦both:вычислительные мощности|compute⟧`,
`⟦ui:Dictation|голосовой ввод⟧`), and `render_markers` decides the first
occurrence in code: walking the segments in time order across all blocks, the
first `both` becomes "rendering (English)", later ones the rendering alone; the
first `ui` becomes "English (gloss)", later ones the English alone. Malformed or
unclosed markers are stripped to their visible text and counted in
`meta.translation.marker_warnings`. Its validator rejects a block where a
`keep` term of the source is missing from the translation (left out,
transliterated or replaced by a pronoun), for every keep term that looks like a
name or code (`strict_keep`: a capital letter, a digit, a symbol or several
words); a plain lower-case word kept for code (`call`, `view` in a Solidity
glossary) is an everyday English word too, so its absence is only a warning. v3
does not enforce agreed translations (the prompt does).

A kept term (`keep: yes`) missing from a segment's translation is recorded in
`meta.translation.warnings`, not rejected (v1, v2): compressing a subtitle may drop a
repeated name ("Claude Code" -> "он"), which the prompt asks for. Translation
term accuracy is measured from the output, so the warnings are evidence, not a
gate. (Changed 2026-11-09 after the first T2 run rejected a block for exactly
this; docs/experiments.md, 3.6.)

**Sentence units** (default since 2026-11-10, `units=True`): the corrected
segments are first regrouped into sentence units (units.py) and the units are
translated, each as one `[id] text` line of the same prompt; the validator works
per unit. The result's segments are the units: new ids, the unit's times, the
English words with their timings (`words`, which the subtitles use to time the
target text) and the covered segment ids (`source_ids`). With `units=False`
(`translate --per-segment`) each segment is translated alone, the result has
the input's ids and times, and word timings are dropped.
"""

import logging
import re
import time
from pathlib import Path

from pydantic import BaseModel

from talk_autopsy import blocks, config, metrics
from talk_autopsy import units as units_module
from talk_autopsy.core import claude_client
from talk_autopsy.core.structured_call import InvalidResultError, Usage, request_structured
from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Segment, Transcript, assert_same_ids

logger = logging.getLogger(__name__)

PROMPT_VERSION = "translation-v2.1"  # translation-v2's rules, examples from outside the test set (2026-11-11)
VERSIONS = ("translation-v1", "translation-v2", "translation-v2.1", "translation-v3")
V3 = "translation-v3"
# A well-formed marker: ⟦both:rendering|English⟧ or ⟦ui:English|gloss⟧ (no nesting, no extra bars).
MARKER = re.compile(r"⟦(both|ui):([^|⟦⟧]+)\|([^|⟦⟧]+)⟧")
# Anything else that starts a marker (missing close, bar or kind); visible text is group 1. Without a
# close, only Latin words after the bar are taken as the marker's English, so the sentence after it stays.
_LATIN_WORDS = r"[A-Za-z0-9][A-Za-z0-9._+#/-]*(?: [A-Za-z0-9][A-Za-z0-9._+#/-]*)*"
BROKEN_MARKER = re.compile(r"⟦(?:(?:both|ui):)?([^|⟦⟧]*)(?:\|[^⟦⟧|]*?⟧|\|(?:" + _LATIN_WORDS + r")?)?⟧?")
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


def _prefix(prompt: str, glossary: Glossary, context: blocks.VideoContext, target_lang: str,
            version: str = PROMPT_VERSION) -> str:
    terms = policy_glossary_text(glossary, target_lang) if version == V3 else blocks.glossary_text(
        glossary, target_lang)
    return (f"{prompt}\n\n## Target language\n\n{target_lang} (ISO 639 code)\n\n"
            f"## Glossary\n\n{terms}\n\n"
            f"## Video context\n\n{context.text()}\n")


# --- translation-v3: term policies, markers, keep validator ---------------------------------


def policy_glossary_text(glossary: Glossary, target_lang: str) -> str:
    """The glossary for translation-v3: `- term [policy] -> rendering (notes)`, one line per term."""
    lines = []
    for e in glossary.entries:
        policy = e.effective_policy() or "unclassified: decide by the rules"
        line = f"- {e.term} [{policy}]"
        rendering = e.translations.get(target_lang)
        if rendering and policy != "keep":
            line += f" -> {rendering}"
        if e.notes:
            line += f" ({e.notes})"
        lines.append(line)
    return "\n".join(lines) if lines else "(no glossary)"


def _strip_broken(text: str) -> str:
    """Malformed markers -> their visible text; stray brackets and bars removed, spacing tidied."""
    text = BROKEN_MARKER.sub(lambda m: m.group(1) + " ", text).replace("⟧", " ").replace("|", " ")
    text = re.sub(r"[ ]{2,}", " ", text)
    return re.sub(r" ([,.;:!?)»])", r"\1", text).strip()


def visible_text(text: str) -> str:
    """A translation with every marker replaced by what stays after the first occurrence."""
    return _strip_broken(MARKER.sub(lambda m: m.group(2), text))


def render_markers(texts: list[str]) -> tuple[list[str], list[str]]:
    """
    Markers -> text, `texts` in time order (the whole video): the first `both`
    of an English term -> "rendering (English)", later -> "rendering"; the first
    `ui` -> "English (gloss)", later -> "English". Returns the texts and the
    warnings for malformed markers (stripped to their visible text).
    """
    seen: set[tuple[str, str]] = set()
    warnings: list[str] = []
    out = []
    for index, text in enumerate(texts):
        def well_formed(m: re.Match) -> str:
            kind, first, second = m.group(1), m.group(2).strip(), m.group(3).strip()
            english = second if kind == "both" else first
            key = (kind, english.casefold())
            if key in seen:
                return first
            seen.add(key)
            return f"{first} ({second})"
        rendered = MARKER.sub(well_formed, text)
        if "⟦" in rendered or "⟧" in rendered:
            broken = BROKEN_MARKER.findall(rendered) or ["⟧"]
            warnings.append(f"segment {index + 1} in time order: {len(broken)} malformed marker(s) stripped")
            rendered = _strip_broken(rendered)
        out.append(re.sub(r"[ ]{2,}", " ", rendered))
    return out, warnings


_PLAIN_WORD = re.compile(r"^[a-z]+$")


def strict_keep(term: str) -> bool:
    """A keep term that looks like a name or code (rejected when missing), not a plain lower-case word."""
    return not _PLAIN_WORD.fullmatch(term)


def validate_block_v3(segments: list[Segment], glossary: Glossary, target_lang: str,
                      answer: TranslationAnswer) -> list[str]:
    """translation-v3: ids, no empty segment, not left in English, strict keep terms present."""
    want = [s.id for s in segments]
    got = [s.id for s in answer.segments]
    if got != want:
        return [f"segment ids {got} differ from the input {want}"]
    problems = []
    for source, out in zip(segments, answer.segments, strict=True):
        text = visible_text(out.text)
        if not text.strip():
            problems.append(f"segment {out.id} is empty")
            continue
        if left_in_english(source.text, text, glossary):
            problems.append(f"segment {out.id} is left in English: {text[:60]!r}")
        for entry in glossary.entries:
            if entry.effective_policy() != "keep" or not strict_keep(entry.term):
                continue
            if not metrics.term_accuracy(source.text, source.text, [entry.term]).rows:
                continue
            if entry.term.casefold() not in text.casefold():
                problems.append(f"segment {out.id}: keep term {entry.term!r} is missing (left out, "
                                "transliterated or replaced by a pronoun); write it in Latin script as is")
    return problems


def _user(block: blocks.Block) -> str:
    parts = []
    if block.context:
        parts.append("Context segments (read-only, do not return):\n" + blocks.numbered(block.context))
    parts.append("Segments to translate:\n" + blocks.numbered(block.segments))
    return "\n\n".join(parts)


def estimate(transcript: Transcript, glossary: Glossary, target_lang: str,
             context: blocks.VideoContext | None = None, model: str | None = None,
             version: str = PROMPT_VERSION, units: bool = True) -> blocks.Estimate:
    model = model or config.translation_model()
    target_lang = config.check_lang(target_lang)
    if units:
        transcript = units_module.make_units(transcript)
    prefix_tokens = blocks.estimate_tokens(_prefix(load_prompt(version), glossary,
                                                   context or blocks.VideoContext(), target_lang, version))
    parts = blocks.make_blocks(transcript)
    input_tokens = output_tokens = 0
    for block in parts:
        input_tokens += prefix_tokens + blocks.estimate_tokens(_user(block))
        output_tokens += round(OUTPUT_FACTOR * blocks.estimate_tokens(blocks.numbered(block.segments)))
    return blocks.Estimate(len(parts), input_tokens, output_tokens,
                           blocks.price(model, input_tokens, output_tokens))


def translate(transcript: Transcript, glossary: Glossary, target_lang: str, *, model: str | None = None,
              context: blocks.VideoContext | None = None, version: str = PROMPT_VERSION,
              progress=None, refusal_fallback: bool = False, units: bool = True) -> Transcript:
    """
    The translated transcript, texts in `target_lang`, usage in `meta.translation`:
    one segment per sentence unit (`units=True`), or the input's ids and times
    (`units=False`).
    """
    config.require_api_key()
    target_lang = config.check_lang(target_lang)
    model = model or config.translation_model()
    if units:
        transcript = units_module.make_units(transcript)
    prefix = _prefix(load_prompt(version), glossary, context or blocks.VideoContext(), target_lang, version)
    validator = validate_block_v3 if version == V3 else validate_block
    client = claude_client.get_client()
    texts: dict[int, str] = {}
    failed: list[dict] = []
    warnings: list[str] = []
    usage = Usage()
    served_by: set[str] = set()
    started = time.perf_counter()
    parts = blocks.make_blocks(transcript)
    for block in parts:
        label = f"{version} block {block.number}/{len(parts)}"
        try:
            response = request_structured(
                client, label=label, model=model, max_tokens=config.MAX_TOKENS, system="",
                user=_user(block), output_format=TranslationAnswer, cached_prefix=prefix,
                refusal_fallback=refusal_fallback,
                validate=lambda answer, segments=block.segments: validator(
                    segments, glossary, target_lang, answer),
            )
        except InvalidResultError as e:
            blocks.merge_usage(usage, e.usage)
            failed.append({"block": block.number, "ids": [s.id for s in block.segments], "error": str(e)})
            logger.warning("%s failed validation twice; its segments stay in English: %s", label, e)
        else:
            blocks.merge_usage(usage, response.usage)
            served_by.add(response.model or model)
            texts.update({s.id: s.text for s in response.result.segments})
            warnings.extend(kept_terms_missing(block.segments, glossary, response.result))
        if progress is not None:
            progress(block.number, len(parts))

    marker_warnings: list[str] = []
    if version == V3:  # first occurrences decided in code, over the whole video in time order
        ordered = sorted((s for s in transcript.segments if s.id in texts), key=lambda s: (s.start, s.id))
        rendered, marker_warnings = render_markers([texts[s.id] for s in ordered])
        texts.update({s.id: text for s, text in zip(ordered, rendered, strict=True)})

    # a unit keeps its English words: the subtitles time the target text by them
    segments = [Segment(id=s.id, start=s.start, end=s.end, text=texts.get(s.id, s.text),
                        words=s.words if units else None, confidence=None, source_ids=s.source_ids)
                for s in transcript.segments]
    meta = blocks.stage_meta(transcript.meta, blocks.usage_cost(model, usage), time.perf_counter() - started)
    meta["translation"] = {
        "prompt_version": version,
        "model": model,
        "served_by": sorted(served_by),  # differs from model only after a refusal fallback
        "refusal_fallback": refusal_fallback,
        "target_lang": target_lang,
        "blocks": len(parts),
        "failed_blocks": failed,
        "warnings": warnings,
        **({"marker_warnings": marker_warnings} if version == V3 else {}),
        "units": transcript.meta.get("units") if units else None,
        "usage": usage.as_dict(),
        "cost_usd": blocks.usage_cost(model, usage),
        "input_source": transcript.source,
    }
    result = Transcript(segments=segments, language=target_lang,
                        source=f"{transcript.source} + {version}", meta=meta)
    assert_same_ids(transcript, result)
    return result
