"""
The frozen prompts carry no example taken from the test set (external audit #1,
finding 5; 2026-11-11). Every backticked or double-quoted item of a frozen prompt
is an example unless it is a field name or a generic discourse marker listed
below; no example may occur in a test-set glossary, and, where the references are
present (local only, never in CI), in a reference either. On a sealed fragment the
failure names the example, never the fragment's text.
"""

import re

import pytest

from talk_autopsy import config, correct, glossary_auto, metrics, translate
from talk_autopsy.glossary import parse_glossary

FROZEN_PROMPTS = (glossary_auto.PROMPT_VERSION, correct.PROMPT_VERSION, translate.PROMPT_VERSION)
FRAGMENTS = ("T1", "T2", "T3", "T4", "T5", "T6")

# Not examples: output field names, input notation and the prompts' own labels.
NOT_EXAMPLES = {
    "+", "term", "heard_as", "kind", "keep", "translation", "note", "segments", "edits", "id", "before",
    "after", "reason", "[id] text", "heard as: ...", "keep as is", "suggested ...", "term -> translation",
    "product name",
}
# Discourse markers occur in any speech, so no example of the class can avoid every reference.
DISCOURSE_MARKERS = {"you know", "like", "I mean", "so"}

_QUOTED = re.compile(r"`([^`]+)`|\"([^\"]+)\"")


def prompt_examples(version: str) -> list[str]:
    text = (translate.PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8")
    found = {" ".join((a or b).split()) for a, b in _QUOTED.findall(text)}
    return sorted(found - NOT_EXAMPLES)


def _occurs(needle: str, haystack: str) -> bool:
    if not metrics.tokenize(needle):
        return False
    return bool(metrics.term_occurrences(haystack.lower(), [needle.lower()])[0])


def _glossary_terms() -> dict[str, list[str]]:
    terms = {}
    for fragment in FRAGMENTS:
        path = config.TEST_SET_DIR / f"{fragment}.glossary.md"
        if path.is_file():
            terms[fragment] = [e.term for e in parse_glossary(path.read_text(encoding="utf-8")).entries]
    return terms


def test_the_tracked_glossaries_are_checked():
    assert {"T1", "T2"} <= set(_glossary_terms())


def test_the_extractor_sees_the_examples():
    assert "PostgreSQL" in prompt_examples("glossary-auto-v2")
    assert "under heavy load" in prompt_examples("translation-v2.1")
    assert "Shift+Enter" in prompt_examples("glossary-auto-v1")  # the old prompt's T2 example


@pytest.mark.parametrize("version", FROZEN_PROMPTS)
def test_no_prompt_example_occurs_in_a_test_set_glossary(version):
    conflicts = []
    for fragment, terms in _glossary_terms().items():
        for example in prompt_examples(version):
            if example in DISCOURSE_MARKERS:
                continue
            if any(_occurs(example, term) or _occurs(term, example) for term in terms):
                conflicts.append(f"{example!r} ({fragment} glossary)")
    assert not conflicts, f"{version}: examples from the test set: {', '.join(conflicts)}"


@pytest.mark.parametrize("version", FROZEN_PROMPTS)
def test_no_prompt_example_occurs_in_a_local_reference(version):
    references = {}
    for fragment in FRAGMENTS:
        for path in sorted(config.TEST_SET_DIR.glob(f"{fragment}.ref*.txt")):
            references[path.name] = path.read_text(encoding="utf-8")
    if not references:
        pytest.skip("no reference transcripts here (they are local only)")
    conflicts = [f"{example!r} ({name})" for name, text in references.items()
                 for example in prompt_examples(version)
                 if example not in DISCOURSE_MARKERS and _occurs(example, text)]
    assert not conflicts, f"{version}: examples from the test set: {', '.join(conflicts)}"


def test_the_old_prompts_fail_the_check():
    # The check would have caught the prompts the audit found.
    terms = [t for terms in _glossary_terms().values() for t in terms]
    old = [e for e in prompt_examples("glossary-auto-v1") if any(_occurs(e, t) for t in terms)]
    assert {"Shift+Enter", "GitHub", "tmux"} <= set(old)
