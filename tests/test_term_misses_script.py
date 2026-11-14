"""scripts/term_misses.py: one row per missed reference-glossary occurrence, with a cause."""

import importlib.util
from pathlib import Path

from talk_autopsy.glossary import Glossary, GlossaryEntry

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("term_misses", ROOT / "scripts" / "term_misses.py")
term_misses = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(term_misses)

TERMS = ("message.sender", "Encoding.sol", "VaultNFT", "Ledger", "string.concat")
GLOSSARY = Glossary(entries=[GlossaryEntry(term=t, keep=True, notes="identifier") for t in TERMS])
REFERENCE = ("first we read message.sender then we open Encoding.sol and the VaultNFT script "
             "and the Ledger file and string.concat at the end")


def rows(hypothesis):
    return {r["term"]: r for r in term_misses.misses(REFERENCE, REFERENCE, hypothesis, GLOSSARY)}


def test_causes_and_code_forms():
    out = rows("first we read msg.sender then we open encoding.sol and the Vault NFT script "
               "and the Lodger file and String.concat at the end")
    assert out["message.sender"]["cause"] == "reference doubt" and out["message.sender"]["code"] == "pipeline"
    assert out["Encoding.sol"]["cause"] == "form (case)" and out["Encoding.sol"]["pipeline"] == "encoding.sol"
    assert out["VaultNFT"]["cause"].startswith("form (") and out["VaultNFT"]["pipeline"] == "Vault NFT"
    assert out["Ledger"]["cause"] == "recognition"
    assert out["string.concat"]["code"] == "reference"


def test_a_hit_is_not_a_row_and_a_neighbour_join_is_a_form():
    out = rows("first we read message.sender then we open Encoding.sol and the VaultNFT script "
               "and the Ledger-file and string.concat at the end")
    assert set(out) == {"Ledger"} and out["Ledger"]["cause"] == "form (joined to a neighbour word)"
