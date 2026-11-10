"""
Reference glossary of a test-set fragment drafted by a non-Claude model
(evaluation tooling): Gemini reads `T<n>.ref.txt` and lists the terms that
term accuracy should score.

    T<n>.ref.txt -> Gemini (structured JSON, text only) -> terms
      -> drop terms that do not occur in the reference (metrics.term_accuracy)
      -> data/test-set/T<n>.glossary.md (glossary.py format)

Why a non-Claude model: the stage 2 correction is Claude, and a glossary
written by the model being scored would favour it (the T1 lesson). For the
sealed T6 the owner no longer writes glossaries, so this is the method there.

Every kept term occurs in the reference as `eval` counts it
(`metrics.term_accuracy` on the whole term list: a short term that occurs
only inside a longer one, `Claude` inside `Claude Code`, counts as absent and
is dropped). At most `MAX_TERMS` are kept, in the model's order (most
important first); fewer than `MIN_TERMS` is reported, never padded.
"""

import time
from dataclasses import dataclass

from talk_autopsy import metrics
from talk_autopsy.glossary import Glossary, GlossaryEntry, to_markdown
from talk_autopsy.judge import GeminiCaller, JudgeError, tokens_of

PROMPT_VERSION = "ref-glossary-v1"
MIN_TERMS, MAX_TERMS = 20, 60

PROMPT = """\
Below is the reference transcript of a fragment of an English technical talk. Extract its \
glossary: the terms a speech recognizer, a corrector or a translator must get exactly right.

Include: product, tool, company and person names; commands, flags, file names and other \
identifiers; technical terms and acronyms. Do not include common English words or phrases \
that need no special care.

Rules:
- Write each term exactly as it occurs in the transcript (its canonical spelling there). Only \
terms that occur in the transcript, word for word.
- Use the base form: singular nouns (`pull request`, not `pull requests`).
- `keep`: true if the term stays untranslated in a translation (names, commands, identifiers, \
acronyms, product features named as such); false for technical terms that are normally translated.
- `notes`: a short category, such as product, company, person, command, acronym, technical term.
- Between 30 and 60 terms, the most important first. Fewer if the transcript has fewer.

Transcript:
{reference}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"term": {"type": "string"}, "keep": {"type": "boolean"},
                               "notes": {"type": "string"}},
                "required": ["term", "keep", "notes"],
            },
        },
    },
    "required": ["terms"],
}


@dataclass
class Extraction:
    glossary: Glossary
    proposed: int  # terms in the model's answer (after removing duplicates)
    dropped_absent: int  # not in the reference as eval counts terms
    dropped_over_limit: int
    model: str
    tokens: dict
    cost_usd: float  # every response of the call, by the judge's price table


def select_terms(reference: str, proposed: list[dict]) -> tuple[list[GlossaryEntry], int, int]:
    """Deduplicate, keep the terms that occur in the reference, cap at MAX_TERMS."""
    entries, seen = [], set()
    for item in proposed:
        term = " ".join(str(item.get("term", "")).split())
        if not term or term.casefold() in seen or not metrics.tokenize(term):
            continue
        seen.add(term.casefold())
        notes = str(item.get("notes", ""))
        entries.append(GlossaryEntry(term=term, keep=bool(item.get("keep")), notes=notes))
    absent = set(metrics.term_accuracy(reference, reference, [e.term for e in entries]).absent)
    present = [e for e in entries if e.term not in absent]
    return present[:MAX_TERMS], len(entries) - len(present), max(0, len(present) - MAX_TERMS)


def extract(reference: str, model: str, sleep=time.sleep, thinking_level: str | None = None) -> Extraction:
    import json

    caller = GeminiCaller(model, pace_seconds=0.0, sleep=sleep, thinking_level=thinking_level)
    response = caller.generate([PROMPT.format(reference=reference)], SCHEMA)
    try:
        data = json.loads(response.text or "")
        proposed = data["terms"]
        if not isinstance(proposed, list):
            raise TypeError("terms is not a list")
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise JudgeError(f"the glossary answer is malformed ({type(e).__name__})") from None
    entries, absent, over = select_terms(reference, [p for p in proposed if isinstance(p, dict)])
    return Extraction(Glossary(entries=entries), len(entries) + absent + over, absent, over, model,
                      tokens_of(response.usage_metadata), round(caller.spent_usd, 6))


def glossary_file(fragment: str, extraction: Extraction, date: str) -> str:
    return (f"# {fragment} glossary\n\n"
            f"Terms of {fragment} extracted from the reference transcript by {extraction.model} "
            f"({PROMPT_VERSION}) on {date};\nevery term occurs in the reference. Not reviewed by a person. "
            "Translations are empty. Format: `talk_autopsy/glossary.py`.\n\n"
            + to_markdown(extraction.glossary, languages=["ru"]))
