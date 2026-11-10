"""
Auto-glossary (hypothesis B5): a glossary drafted by Claude from the first
speech recognition pass, so an unseen video gets key terms and spellings
without anyone writing a glossary by hand.

    raw.json (+ optional context text) -> glossary.auto.md

One structured call to the helper model (`config.helper_model()`, Haiku) with the
whole transcript as plain text, one segment per line, and the context (video
title, description, chapter titles) if the owner has any. For a local file the
file name is not context. The prompt is `prompts/glossary-auto-v1.md`.

The answer lists 20-60 terms, each with a canonical spelling, `keep`, a
translation into `target_lang` when not kept, and `heard_as`: the forms in the
transcript the model takes for mis-recognitions of the term. That last field is
what makes the glossary useful for the second recognition pass and for
correction: it says which distorted forms the canonical spelling replaces.

Evidence rule: a term whose canonical spelling and every `heard_as` form are
absent from the transcript is dropped (listed in the result, never in the
glossary), and `heard_as` forms not in the transcript are removed. The model
may only name what the transcript shows.

The output is the documented glossary table (glossary.py) with the header
comment `<!-- auto-generated: <model>, <date> -->`; `heard_as` and the kind go
to the notes column.

`coverage` compares an auto-glossary with a reference glossary using the
metrics' term-matching rule (identifiers case-sensitive, a regular plural of
the last word accepted), and `merge` joins an auto-glossary with a user
glossary that wins on conflicts (the pipeline's glossary step).
"""

import datetime as dt
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from talk_autopsy import blocks, config, metrics
from talk_autopsy.core import claude_client
from talk_autopsy.core.structured_call import Usage, request_structured
from talk_autopsy.glossary import Glossary, GlossaryEntry, to_markdown
from talk_autopsy.segments import Transcript

logger = logging.getLogger(__name__)

PROMPT_VERSION = "glossary-auto-v1"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
MAX_TERMS = 60
MAX_TERM_WORDS = 6  # AssemblyAI's key-term limit; longer terms would be left out of the second pass
OUTPUT_TOKENS_ESTIMATE = 6000  # 60 terms with notes, plus adaptive thinking
HEADER = "<!-- auto-generated: {model}, {date} -->"


class AutoTerm(BaseModel):
    term: str
    heard_as: list[str]
    kind: str
    keep: bool
    translation: str
    note: str


class AutoGlossaryAnswer(BaseModel):
    terms: list[AutoTerm]


@dataclass
class AutoGlossaryResult:
    glossary: Glossary
    markdown: str
    model: str
    usage: Usage
    cost_usd: float
    wall_seconds: float
    dropped: list[str] = field(default_factory=list)  # terms without evidence in the transcript
    heard_as: dict[str, list[str]] = field(default_factory=dict)  # term -> distorted forms kept


def load_prompt(version: str = PROMPT_VERSION) -> str:
    return (PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8")


def read_context(value: str | None) -> str:
    """`--context`: the text of a file if `value` names one, else `value` itself; None -> empty."""
    if not value:
        return ""
    path = Path(value)
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except OSError:  # a long literal text is not a valid path on every system
        pass
    return value.strip()


def transcript_text(transcript: Transcript) -> str:
    return "\n".join(s.text.strip() for s in transcript.segments if s.text.strip())


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def occurs(form: str, text: str) -> bool:
    """
    `form` occurs in `text`, ignoring case and runs of whitespace, not inside a
    longer word; the last word may be in a regular plural (the glossary keeps the
    base form: `diff` is evidenced by "diffs").
    """
    wanted = _norm(form)
    if not wanted:
        return False
    head, _, last = wanted.rpartition(" ")
    variants = [wanted, *(f"{head} {p}".strip() for p in metrics.plural_forms(last))]
    haystack = _norm(text)
    return any(re.search(r"(?<!\w)" + re.escape(v) + r"(?!\w)", haystack) for v in variants)


def validate(answer: AutoGlossaryAnswer) -> list[str]:
    """Problems with the answer as a whole (a retry is worth it); single bad terms are dropped instead."""
    problems = []
    if not answer.terms:
        problems.append("no terms")
    if len(answer.terms) > MAX_TERMS:
        problems.append(f"{len(answer.terms)} terms, at most {MAX_TERMS}")
    empty = sum(1 for t in answer.terms if not t.term.strip())
    if empty:
        problems.append(f"{empty} empty term(s)")
    return problems


def _system(prompt: str, target_lang: str) -> str:
    return f"{prompt}\n\n## Target language\n\n{target_lang} (ISO 639 code)\n"


def _user(text: str, context: str) -> str:
    parts = [f"Video context:\n{context}" if context else "Video context: none"]
    parts.append(f"Transcript (one segment per line):\n{text}")
    return "\n\n".join(parts)


def estimate(transcript: Transcript, target_lang: str, context: str = "", model: str | None = None,
             version: str = PROMPT_VERSION) -> blocks.Estimate:
    model = model or config.helper_model()
    input_tokens = blocks.estimate_tokens(_system(load_prompt(version), target_lang)
                                          + _user(transcript_text(transcript), context))
    return blocks.Estimate(1, input_tokens, OUTPUT_TOKENS_ESTIMATE,
                           blocks.price(model, input_tokens, OUTPUT_TOKENS_ESTIMATE))


def build_glossary(answer: AutoGlossaryAnswer, text: str, target_lang: str
                   ) -> tuple[Glossary, list[str], dict[str, list[str]]]:
    """Glossary entries from the answer: evidence rule, duplicates and over-long terms removed."""
    entries: list[GlossaryEntry] = []
    seen: set[str] = set()
    dropped: list[str] = []
    heard: dict[str, list[str]] = {}
    for t in answer.terms:
        term = re.sub(r"\s+", " ", t.term).strip()
        if not term or term.casefold() in seen:
            continue
        if len(term.split()) > MAX_TERM_WORDS:
            dropped.append(f"{term} (over {MAX_TERM_WORDS} words)")
            continue
        forms = list(dict.fromkeys(f.strip() for f in t.heard_as
                                   if f.strip() and f.strip() != term and occurs(f, text)))
        if not occurs(term, text) and not forms:
            dropped.append(f"{term} (not in the transcript)")
            continue
        seen.add(term.casefold())
        notes = "; ".join(p for p in (t.kind.strip(), t.note.strip(),
                                      f"heard as: {', '.join(forms)}" if forms else "") if p)
        translation = "" if t.keep else t.translation.strip()
        entries.append(GlossaryEntry(term=term, keep=t.keep,
                                     translations={target_lang: translation} if translation else {},
                                     notes=notes))
        if forms:
            heard[term] = forms
    return Glossary(entries=entries), dropped, heard


def markdown(glossary: Glossary, model: str, target_lang: str, date: dt.date | None = None) -> str:
    header = HEADER.format(model=model, date=(date or dt.date.today()).isoformat())
    return f"{header}\n\n{to_markdown(glossary, [target_lang])}"


def generate(transcript: Transcript, target_lang: str, context: str = "", *, model: str | None = None,
             version: str = PROMPT_VERSION) -> AutoGlossaryResult:
    """Draft the glossary of `transcript` (one call to the helper model)."""
    config.require_api_key()
    target_lang = config.check_lang(target_lang)
    model = model or config.helper_model()
    text = transcript_text(transcript)
    if not text:
        raise ValueError(f"{transcript.source}: the transcript has no text")
    started = time.perf_counter()
    response = request_structured(
        claude_client.get_client(), label=version, model=model, max_tokens=config.MAX_TOKENS,
        system=_system(load_prompt(version), target_lang), user=_user(text, context),
        output_format=AutoGlossaryAnswer, validate=validate,
    )
    glossary, dropped, heard = build_glossary(response.result, text, target_lang)
    if dropped:
        logger.info("auto-glossary: dropped %d term(s): %s", len(dropped), "; ".join(dropped))
    return AutoGlossaryResult(
        glossary=glossary, markdown=markdown(glossary, model, target_lang), model=model,
        usage=response.usage, cost_usd=blocks.usage_cost(model, response.usage),
        wall_seconds=round(time.perf_counter() - started, 1), dropped=dropped, heard_as=heard,
    )


# --- comparison with a reference glossary -------------------------------------------


def same_term(candidate: str, reference: str) -> bool:
    """
    True if `candidate` spells the reference term by the metrics' rule: the same
    number of tokens, identifiers with their exact case, the last word possibly
    in a regular plural.
    """
    if len(metrics.tokenize(candidate)) != len(metrics.tokenize(reference)):
        return False
    return bool(metrics.term_accuracy(candidate, candidate, [reference]).rows)


@dataclass(frozen=True)
class Coverage:
    reference_terms: int
    covered: list[str]  # reference terms present with the right spelling
    wrong_case: list[tuple[str, str]]  # (reference term, auto spelling) equal except for case
    missing: list[str]
    noise: list[str]  # auto terms that match no reference term

    @property
    def share(self) -> float | None:
        return len(self.covered) / self.reference_terms if self.reference_terms else None


def coverage(auto: Glossary, reference: Glossary) -> Coverage:
    covered, wrong_case, missing = [], [], []
    matched_auto: set[str] = set()
    for ref in reference.entries:
        hit = next((a.term for a in auto.entries if same_term(a.term, ref.term)), None)
        if hit is not None:
            covered.append(ref.term)
            matched_auto.add(hit)
            continue
        near = next((a.term for a in auto.entries if _norm(a.term) == _norm(ref.term)), None)
        if near is not None:
            wrong_case.append((ref.term, near))
            matched_auto.add(near)
        else:
            missing.append(ref.term)
    noise = [a.term for a in auto.entries if a.term not in matched_auto]
    return Coverage(len(reference.entries), covered, wrong_case, missing, noise)


# --- merge with a user glossary -------------------------------------------------------


def as_suggestions(entry: GlossaryEntry) -> GlossaryEntry:
    """
    An auto entry whose translations move into its notes as "suggested <lang>:
    ...". The translation stage enforces agreed translations (a block that
    does not use one is rejected); a model's draft is not agreed, so it goes to
    the translator as a suggestion instead.
    """
    if not entry.translations:
        return entry
    hints = [f"suggested {lang}: {text}" for lang, text in sorted(entry.translations.items())]
    notes = "; ".join(p for p in (entry.notes, *hints) if p)
    return GlossaryEntry(term=entry.term, keep=entry.keep, translations={}, notes=notes)


def merge(auto: Glossary, user: Glossary | None) -> Glossary:
    """
    The glossary of a run: the user's entries first and unchanged (they win on a
    conflict, matched by `Glossary.find`, case-insensitive; their translations
    are agreed), then the auto entries the user glossary does not have, with
    their translations turned into suggestions (`as_suggestions`).
    """
    user_entries = list(user.entries) if user is not None else []
    known = Glossary(entries=user_entries)
    return Glossary(entries=user_entries + [as_suggestions(a) for a in auto.entries
                                            if known.find(a.term) is None])
