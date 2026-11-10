"""
Term classification for translation-v3 (translation side only; added 2026-11-10).

    glossary.md (frozen auto-glossary merged with the user's) -> glossary.v3.md

After the frozen auto-glossary step, one call to the helper model (Haiku)
assigns a translation `policy` (keep / both / translate / ui, glossary.py) and
a target-language rendering to every term that has no policy yet. The rules
and examples are the owner's term policy of 2026-11-10 (docs/decisions.md).

What it does not do: add or remove terms, change a term's spelling, or touch
what speech recognition or correction see. The classified glossary is a
separate file read only by the translation step; `glossary.md`, from which the
second speech recognition pass takes its key terms, is never rewritten.

User glossary wins: a term with an explicit policy (a `policy` column) keeps
it, and a term that comes from the user's glossary keeps the policy its `keep`
column implies (`GlossaryEntry.effective_policy`). Only the other terms (the
auto-glossary's) are classified.
"""

import time
from dataclasses import dataclass

from pydantic import BaseModel

from talk_autopsy import blocks, config
from talk_autopsy.core import claude_client
from talk_autopsy.core.structured_call import Usage, request_structured
from talk_autopsy.glossary import POLICIES, Glossary, GlossaryEntry

VERSION = "term-policy-v1"
OUTPUT_TOKENS_PER_TERM = 40

PROMPT = """\
You classify the glossary terms of an English technical talk for its translation into the target \
language named below. For every term, choose how the translation renders it, and give the rendering.

Policies:
- keep: written in Latin script exactly as is. Names of products, companies, people, tools, \
libraries; code, commands, identifiers, file names; acronyms without an established form in the \
target language. Examples: Claude, OpenAI, Foundry, msg.sender, forge test, API, GPU.
- translate: an ordinary word or phrase with a normal translation; the translation only, no English. \
Examples (Russian): computer science -> информатика; tokens -> токены; thinking tokens -> токены \
рассуждений; frontier model -> передовая модель; "prompt you" -> задать вам вопрос; emergent \
properties -> свойства, которые появляются при большом масштабе.
- both: jargon without a good word in the target language; a rendering in the target language, the \
English term goes in brackets at its first occurrence (done later in code). Examples (Russian): \
compute -> вычислительные мощности; inference -> запуск моделей; alignment -> согласование ИИ с \
интересами людей.
- ui: the name of a button, menu, setting or panel as shown on screen; stays in English, the \
rendering is a short gloss in the target language. Examples (Russian): Dictation -> голосовой ввод; \
Command Palette -> палитра команд.

Rules:
- Prefer translate when the target language has a normal word developers would understand; use both \
only when a plain translation would lose the technical meaning; use keep only for names, code and \
acronyms.
- Rare scientific loanwords are not a rendering (in Russian: not «эмерджентный», «аффорданс»); use \
plain words.
- `rendering`: empty for keep; otherwise the rendering in the target language, in its base form.
- Return every term exactly once, spelled exactly as given. Do not add or remove terms.

Target language: {lang} (ISO 639 code)

Terms (with the glossary's notes):
{terms}"""


class TermPolicy(BaseModel):
    term: str
    policy: str
    rendering: str


class TermPolicyAnswer(BaseModel):
    terms: list[TermPolicy]


@dataclass
class ClassifyResult:
    glossary: Glossary
    classified: int  # terms the model classified
    kept: int  # terms whose policy came from the user glossary or a policy column
    model: str
    usage: Usage
    cost_usd: float
    wall_seconds: float


def to_classify(glossary: Glossary, user: Glossary | None) -> list[GlossaryEntry]:
    """Terms the model classifies: no explicit policy and not from the user's glossary."""
    return [e for e in glossary.entries
            if not e.policy and (user is None or user.find(e.term) is None)]


def _terms_text(entries: list[GlossaryEntry]) -> str:
    return "\n".join(f"- {e.term}" + (f" ({e.notes})" if e.notes else "") for e in entries)


def validate(entries: list[GlossaryEntry]):
    wanted = [e.term for e in entries]

    def check(answer: TermPolicyAnswer) -> list[str]:
        got = [t.term for t in answer.terms]
        problems = []
        if sorted(got) != sorted(wanted):
            missing = sorted(set(wanted) - set(got))
            extra = sorted(set(got) - set(wanted))
            problems.append(f"terms differ from the input (missing {missing}, extra {extra}, "
                            f"{len(got)} returned for {len(wanted)})")
        for t in answer.terms:
            if t.policy not in POLICIES:
                problems.append(f"{t.term}: policy {t.policy!r} is not one of {', '.join(POLICIES)}")
            elif t.policy != "keep" and not t.rendering.strip():
                problems.append(f"{t.term}: policy {t.policy} needs a rendering")
        return problems
    return check


def estimate(glossary: Glossary, user: Glossary | None, target_lang: str, model: str | None = None
             ) -> blocks.Estimate:
    model = model or config.helper_model()
    entries = to_classify(glossary, user)
    if not entries:
        return blocks.Estimate(0, 0, 0, 0.0)
    input_tokens = blocks.estimate_tokens(PROMPT.format(lang=target_lang, terms=_terms_text(entries)))
    output_tokens = OUTPUT_TOKENS_PER_TERM * len(entries)
    return blocks.Estimate(1, input_tokens, output_tokens, blocks.price(model, input_tokens, output_tokens))


def apply(glossary: Glossary, user: Glossary | None, answer: TermPolicyAnswer | None, target_lang: str
          ) -> Glossary:
    """
    The classified glossary: every entry with an explicit policy. User entries
    and policy-column entries keep theirs (an unclassified user entry, keep no
    and no translation, stays without one); the others get the model's policy
    and rendering. `keep` follows the policy so the file stays valid.
    """
    by_term = {t.term: t for t in answer.terms} if answer else {}
    out = []
    for e in glossary.entries:
        if e.policy or (user is not None and user.find(e.term) is not None):
            policy = e.effective_policy()
            out.append(e.model_copy(update={"policy": policy}))
            continue
        t = by_term.get(e.term)
        if t is None:
            out.append(e)
            continue
        translations = dict(e.translations)
        if t.policy != "keep":
            translations[target_lang] = t.rendering.strip()
        else:
            translations.pop(target_lang, None)
        keep = t.policy in ("keep", "ui")
        out.append(GlossaryEntry(term=e.term, keep=keep, translations=translations, notes=e.notes,
                                 policy=t.policy))
    return Glossary(entries=out)


def classify(glossary: Glossary, target_lang: str, user: Glossary | None = None, *,
             model: str | None = None) -> ClassifyResult:
    """One helper-model call for the unclassified terms (none if every term has a policy)."""
    target_lang = config.check_lang(target_lang)
    model = model or config.helper_model()
    entries = to_classify(glossary, user)
    started = time.perf_counter()
    if not entries:
        return ClassifyResult(apply(glossary, user, None, target_lang), 0, len(glossary.entries), model,
                              Usage(), 0.0, 0.0)
    config.require_api_key()
    response = request_structured(
        claude_client.get_client(), label=VERSION, model=model, max_tokens=config.MAX_TOKENS, system="",
        user=PROMPT.format(lang=target_lang, terms=_terms_text(entries)), output_format=TermPolicyAnswer,
        validate=validate(entries))
    classified = apply(glossary, user, response.result, target_lang)
    return ClassifyResult(classified, len(entries), len(glossary.entries) - len(entries), model,
                          response.usage, blocks.usage_cost(model, response.usage),
                          round(time.perf_counter() - started, 1))
