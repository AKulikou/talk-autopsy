"""
Term accuracy with form normalization (E1 guard, v1.2; docs/experiments.md, E1).

The frozen metric (`metrics.term_accuracy_v2`) counts `Mood NFT` for `MoodNFT`, `encoding.sol` for
`Encoding.sol` or `simple_storage.abi` for `SimpleStorage.abi` as misses. On T7 most misses are such
form differences (T7 term-miss breakdown), so E1's guard also reports the accuracy with these forms
treated as equal, and nothing else:

- case-insensitive;
- hyphens, underscores and spaces between the parts of a term are ignored.

How: both texts are tokenized as every metric does (`metrics.tokenize`, lower case), and every run of
up to WINDOW consecutive tokens whose concatenation, with `-` and `_` removed, equals a term's compact
form (lower case, no spaces, hyphens or underscores) is replaced by that compact form as one token
(longest run first, left to right). The terms become their compact forms (duplicates once). Then
`metrics.term_accuracy_v2` runs unchanged on the two normalized texts: the same alignment, the same
position rule, the same plural and possessive allowance. Nothing else changes: other punctuation
inside a token (dots, slashes) and other letters must still be the same.
"""

import re

from talk_autopsy import metrics

_SEP = re.compile(r"[-_\s]+")
EXTRA_WINDOW = 3  # a term's parts may be split into a few more tokens than it has (MoodNFT -> Mood NFT)


def compact(term: str) -> str:
    return _SEP.sub("", term.casefold())


def _window(terms: list[str]) -> int:
    return max((len([p for p in _SEP.split(t) if p]) for t in terms), default=1) + EXTRA_WINDOW


def normalize(text: str, compact_terms: set[str], window: int) -> str:
    tokens = metrics.tokenize(text, lowercase=True)
    out: list[str] = []
    i = 0
    while i < len(tokens):
        for n in range(min(window, len(tokens) - i), 0, -1):
            joined = _SEP.sub("", "".join(tokens[i:i + n]))
            if joined in compact_terms:
                out.append(joined)
                i += n
                break
        else:
            out.append(tokens[i])
            i += 1
    return " ".join(out)


def term_accuracy(reference: str, hypothesis: str, terms: list[str]):
    """`metrics.term_accuracy_v2` after the form normalization above (its result type)."""
    forms = list(dict.fromkeys(compact(t) for t in terms if compact(t)))
    window = _window(terms)
    known = set(forms)
    return metrics.term_accuracy_v2(normalize(reference, known, window), normalize(hypothesis, known, window),
                                    forms)
