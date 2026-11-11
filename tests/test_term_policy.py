"""Term policy in the glossary (keep / both / translate / ui) and the term classification step."""

import re

import pytest

from talk_autopsy import config, term_policy
from talk_autopsy.glossary import (
    Glossary,
    GlossaryEntry,
    GlossaryError,
    load_glossary,
    parse_glossary,
    to_markdown,
)
from talk_autopsy.term_policy import TermPolicy, TermPolicyAnswer
from tests.conftest import response

# --- the policy column -----------------------------------------------------------


def test_policy_column_is_parsed():
    g = parse_glossary("| term | keep | policy | ru | notes |\n|---|---|---|---|---|\n"
                       "| compute | no | both | вычислительные мощности | |\n"
                       "| Dictation | yes | ui | голосовой ввод | |\n"
                       "| Foundry | yes | keep | | |\n"
                       "| tokens | no | translate | токены | |\n")
    assert [e.policy for e in g.entries] == ["both", "ui", "keep", "translate"]
    assert g.find("compute").translations == {"ru": "вычислительные мощности"}


@pytest.mark.parametrize(("row", "message"), [
    ("| x | no | sometimes | y | |", "policy must be one of"),
    ("| x | no | keep | | |", "needs keep yes"),
    ("| x | yes | both | y | |", "needs keep no"),
    ("| x | no | both | | |", "needs a translation"),
])
def test_bad_policy_rows(row, message):
    with pytest.raises(GlossaryError, match=message):
        parse_glossary("| term | keep | policy | ru | notes |\n|---|---|---|---|---|\n" + row + "\n")


def test_backwards_compatible_mapping():
    g = parse_glossary("| term | keep | ru |\n|---|---|---|\n| Claude | yes | |\n"
                       "| subagent | no | субагент |\n"
                       "| worktree | no | |\n")
    assert [e.policy for e in g.entries] == [None, None, None]
    assert [e.effective_policy() for e in g.entries] == ["keep", "translate", None]


def test_markdown_without_policies_is_unchanged_and_with_policies_round_trips():
    plain = Glossary(entries=[GlossaryEntry(term="Claude", keep=True)])
    assert to_markdown(plain).splitlines()[0] == "| term | keep | notes |"
    with_policy = Glossary(entries=[GlossaryEntry(term="compute", keep=False, translations={"ru": "мощности"},
                                                  policy="both")])
    text = to_markdown(with_policy)
    assert text.splitlines()[0] == "| term | keep | policy | ru | notes |"
    assert parse_glossary(text).entries[0].policy == "both"


def test_web3_maps_the_draft_decisions():
    g = load_glossary(config.GLOSSARIES_DIR / "web3.md")
    assert g.find("Foundry").policy == "keep" and g.find("msg.sender").policy == "keep"
    assert g.find("coverage").policy == "both" and g.find("coverage").translations["ru"] == "покрытие"
    assert g.find("smart contract").policy == "translate"
    assert all(e.policy for e in g.entries)


# --- the classification step --------------------------------------------------------


AUTO = Glossary(entries=[GlossaryEntry(term="OpenAI", keep=True),
                         GlossaryEntry(term="tokens", keep=True),
                         GlossaryEntry(term="compute", keep=False,
                                       notes="suggested ru: вычислительные мощности")])
USER = Glossary(entries=[GlossaryEntry(term="tokens", keep=True, notes="the user's decision")])


def classifier_handler(kwargs):
    body = kwargs["messages"][0]["content"]
    terms = re.findall(r"^- (.+?)(?: \(|$)", body.split("Terms (with")[1], re.M)
    table = {"OpenAI": ("keep", ""), "tokens": ("translate", "токены"),
             "compute": ("both", "вычислительные мощности"),
             "Two": ("translate", "два"), "Three": ("keep", "")}
    return response(TermPolicyAnswer(terms=[TermPolicy(term=t, policy=table[t][0], rendering=table[t][1])
                                            for t in terms]))


def test_classification_assigns_policies_and_user_wins(fake_claude):
    fake = fake_claude(classifier_handler)
    merged = Glossary(entries=[USER.entries[0], AUTO.entries[0], AUTO.entries[2]])
    result = term_policy.classify(merged, "ru", USER)
    assert result.classified == 2 and result.kept == 1
    by = {e.term: e for e in result.glossary.entries}
    assert by["tokens"].policy == "keep"  # the user's keep yes wins over the model
    assert by["OpenAI"].policy == "keep" and by["compute"].policy == "both"
    assert by["compute"].translations["ru"] == "вычислительные мощности"
    # none added or removed
    assert [e.term for e in result.glossary.entries] == ["tokens", "OpenAI", "compute"]
    body = fake.requests[0]["messages"][0]["content"]
    assert "- tokens" not in body and fake.requests[0]["model"] == config.helper_model()


def test_classification_rejects_a_changed_term_list():
    check = term_policy.validate(AUTO.entries)
    answer = TermPolicyAnswer(terms=[TermPolicy(term="OpenAI", policy="keep", rendering=""),
                                     TermPolicy(term="GPU", policy="keep", rendering="")])
    problems = check(answer)
    assert any("terms differ" in p for p in problems)
    bad = TermPolicyAnswer(terms=[TermPolicy(term=t.term, policy="both", rendering="") for t in AUTO.entries])
    assert any("needs a rendering" in p for p in check(bad))


def test_no_call_when_every_term_has_a_policy(fake_claude):
    fake = fake_claude(classifier_handler)
    g = Glossary(entries=[GlossaryEntry(term="Foundry", keep=True, policy="keep")])
    assert term_policy.classify(g, "ru").classified == 0 and fake.requests == []
