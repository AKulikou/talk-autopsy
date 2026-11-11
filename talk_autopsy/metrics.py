"""
Metrics for the benchmark. Pure functions: text and numbers in, numbers out, no
file or console I/O (the `eval` command in cli.py reads the files).

Every number in results/benchmark.csv and the README comes from here, so each
function says exactly what it measures and how its input is normalized. Rates
are returned as fractions (0.123), not percentages. The definitions are
summarised in one place for readers: docs/experiments.md, Metrics. The
benchmark's term accuracy is `term_accuracy_v2` (position-aware) and its
false-edit rate is `false_edit_rate` (a share of the correction's edits); the
`_v1` functions are the superseded measures, kept for the record. Timing drift
is not implemented.

Text normalization (shared by WER, term accuracy and false-edit rate) is
`tokenize`: annotation markers such as `[inaudible]` are removed, punctuation is
stripped from word edges but kept inside identifiers, whitespace is collapsed,
the filler sounds uh, um, er, ah and hmm are removed (reference conventions in
data/test-set/README.md), and nothing is expanded (no numbers to words, no
contractions, no spelling variants). WER lower-cases every token; term accuracy keeps the case so that
identifiers can be matched case-sensitively.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import jiwer

# --- Text normalization ----------------------------------------------------

# Annotation markers in a hand-checked reference: [inaudible], [laughter],
# [music], [crosstalk]. Only letters and spaces inside the brackets.
MARKER = r"\[[A-Za-z][A-Za-z ]*\]"

# Characters a token may keep at its start: @scope/package, -g and --flag,
# .env, /clear, ~/.claude, $HOME, #channel.
_KEEP_LEADING = frozenset("@-./~$#")
# Characters a token may keep at its end: C++, C#, src/, 50%.
_KEEP_TRAILING = frozenset("+#/%")
_LEADING_DOTS = re.compile(r"^\.{2,}")  # an ellipsis before a word is punctuation, not .env

# Filler sounds that the reference conventions do not write. They are removed from
# every text before scoring or alignment, so they never count as errors or
# disagreements. "you know", "like", "so", "I mean" are words and stay.
FILLERS = frozenset({"uh", "um", "er", "ah", "hmm"})

_PREPARE = jiwer.Compose([
    jiwer.SubstituteRegexes({
        MARKER: " ",
        "[‘’ʼ]": "'",  # typographic apostrophes -> '
        "[“”]": '"',
        "[–—…]": " ",  # en dash, em dash, ellipsis character: word separators
    }),
    jiwer.RemoveMultipleSpaces(),
    jiwer.Strip(),
])


def _strip_edges(chunk: str) -> str:
    start, end = 0, len(chunk)
    while start < end and not chunk[start].isalnum() and chunk[start] not in _KEEP_LEADING:
        start += 1
    while end > start and not chunk[end - 1].isalnum() and chunk[end - 1] not in _KEEP_TRAILING:
        end -= 1
    return _LEADING_DOTS.sub("", chunk[start:end])


def tokenize(text: str, lowercase: bool = True) -> list[str]:
    """
    Text -> word tokens, the normalization every metric uses.

    1. Annotation markers (`[inaudible]`, `[laughter]`: brackets around letters
       and spaces only) are removed. The words they stand for are unknown, so
       whatever the hypothesis has there counts as insertions.
    2. Typographic apostrophes and quotes become ASCII; en dash, em dash and the
       ellipsis character become spaces.
    3. The text is split on whitespace. Punctuation is stripped from the edges of
       each piece but kept inside it, so identifiers survive as one token:
       `kubectl,` -> `kubectl`, `(useEffect)` -> `useEffect`,
       `@anthropic-ai/claude-code.` -> `@anthropic-ai/claude-code`. A token may
       start with one of `@ - . / ~ $ #` (`-g`, `--steps`, `.env`, `/clear`) and
       end with one of `+ # / %` (`C++`, `src/`, `50%`).
    4. Pieces without a letter or digit (a lone dash, `...`) are dropped.
    5. Filler sounds (`FILLERS`: uh, um, er, ah, hmm; any case) are dropped.
    6. With `lowercase` (the default, used by WER) tokens are lower-cased.

    Nothing is expanded or merged: `10` and `ten`, `open-source` and
    `open source`, `don't` and `do not` are different tokens.
    """
    prepared = _PREPARE(text)
    tokens = []
    for chunk in prepared.split():
        token = _strip_edges(chunk)
        if any(ch.isalnum() for ch in token) and token.casefold() not in FILLERS:
            tokens.append(token.lower() if lowercase else token)
    return tokens


def is_filler(word: str) -> bool:
    """True if `word`, with edge punctuation stripped, is a filler sound (`FILLERS`)."""
    return _strip_edges(_PREPARE(word)).casefold() in FILLERS


def _joined(tokens: Sequence[str]) -> str:
    # Tokens contain no whitespace, so jiwer's own whitespace split restores them exactly.
    return " ".join(tokens)


# --- WER -------------------------------------------------------------------


@dataclass(frozen=True)
class WerResult:
    wer: float
    substitutions: int
    deletions: int
    insertions: int
    hits: int
    reference_words: int


def word_error_rate(reference: str, hypothesis: str) -> WerResult:
    """
    Word error rate of `hypothesis` against `reference`, both normalized with
    `tokenize` (lower-cased).

    WER = (substitutions + deletions + insertions) / reference_words, from the
    minimum-edit word alignment of jiwer (`jiwer.process_words`, the computation
    behind `jiwer.wer`). It can exceed 1 when the hypothesis has many extra words.
    Raises ValueError if the reference has no words.
    """
    ref = tokenize(reference)
    if not ref:
        raise ValueError("the reference has no words after normalization")
    hyp = tokenize(hypothesis)
    out = jiwer.process_words(_joined(ref), _joined(hyp))
    return WerResult(
        wer=out.wer,
        substitutions=out.substitutions,
        deletions=out.deletions,
        insertions=out.insertions,
        hits=out.hits,
        reference_words=len(ref),
    )


# --- Term accuracy ---------------------------------------------------------

_IDENTIFIER_CHARS = frozenset("._-/@")


def is_identifier(token: str) -> bool:
    """
    True if a term token is an identifier and must match with its exact case: it
    has an uppercase letter after the first character (`useEffect`, `GitHub`,
    `API`), a digit (`GPT-5`, `k8s`) or one of `. _ - / @` (`claude-code`, `-g`).
    """
    return (
        any(ch.isupper() for ch in token[1:])
        or any(ch.isdigit() for ch in token)
        or any(ch in _IDENTIFIER_CHARS for ch in token)
    )


@dataclass(frozen=True)
class TermRow:
    term: str
    identifier: bool  # at least one token of the term matches case-sensitively
    expected: int  # occurrences in the reference
    found: int  # of those, how many the hypothesis has: min(expected, occurrences in the hypothesis)


@dataclass(frozen=True)
class TermAccuracyResult:
    rows: list[TermRow]  # terms that occur in the reference, in glossary order
    absent: list[str]  # glossary terms that do not occur in the reference; not in the average
    accuracy: float | None  # sum(found) / sum(expected) over rows; None when no term occurs

    @property
    def expected(self) -> int:
        return sum(row.expected for row in self.rows)

    @property
    def found(self) -> int:
        return sum(row.found for row in self.rows)


def _token_matches(term_token: str, token: str) -> bool:
    # The term decides the case rule: the ordinary word `npm` matches `NPM`, the
    # identifier `GitHub` does not match `Github`.
    return token == term_token if is_identifier(term_token) else token.casefold() == term_token.casefold()


def plural_forms(word: str) -> list[str]:
    """
    Regular English plurals of `word`: `+s`, `+es`, and `y -> ies` after a
    consonant (`diff` -> `diffs`, `box` -> `boxes`, `library` -> `libraries`).
    No irregular plurals.
    """
    forms = [word + "s", word + "es"]
    if len(word) > 1 and word[-1] in "yY" and word[-2].lower() not in "aeiou":
        forms.append(word[:-1] + ("ies" if word[-1] == "y" else "IES"))
    return forms


def _last_token_matches(term_token: str, token: str) -> bool:
    # The last word of a term may also be in a regular plural form: `pull request`
    # matches `pull requests`. The case rule of the term applies to the plural too.
    return _token_matches(term_token, token) or any(
        _token_matches(plural, token) for plural in plural_forms(term_token))


POSSESSIVE = "'s"  # tokenize turns typographic apostrophes into '


def _last_token_matches_possessive(term_token: str, token: str) -> bool:
    # term_accuracy_v2 also accepts a possessive of the last word or of its plural:
    # `Anthropic` matches `Anthropic's`. (A plural possessive, `agents'`, loses its
    # trailing apostrophe in `tokenize` and is a plural already.)
    return _last_token_matches(term_token, token) or (
        token.endswith(POSSESSIVE) and len(token) > len(POSSESSIVE)
        and _last_token_matches(term_token, token[:-len(POSSESSIVE)]))


def _claim(term_tokens: list[str], tokens: list[str], claimed: list[bool],
           possessive: bool = False) -> list[tuple[int, int]]:
    """
    Find the non-overlapping occurrences of `term_tokens` in `tokens` that use no
    position claimed by another term, and claim the positions they use. The last
    token of the term may be in a regular plural form (`plural_forms`) and, with
    `possessive`, a possessive (`'s`). Returns each occurrence's token range
    (start, end), end exclusive.
    """
    last = _last_token_matches_possessive if possessive else _last_token_matches
    found, i, n = [], 0, len(term_tokens)
    while i + n <= len(tokens):
        window = range(i, i + n)
        if not any(claimed[j] for j in window) and all(
            (last if k == n - 1 else _token_matches)(t, tokens[j])
            for k, (t, j) in enumerate(zip(term_tokens, window, strict=True))
        ):
            for j in window:
                claimed[j] = True
            found.append((i, i + n))
            i += n
        else:
            i += 1
    return found


def _find_terms(term_tokens: list[list[str]], tokens: list[str],
                possessive: bool = False) -> list[list[tuple[int, int]]]:
    """Occurrences of each term, longest terms first, so a term inside a longer one is not counted twice."""
    claimed = [False] * len(tokens)
    found: list[list[tuple[int, int]]] = [[] for _ in term_tokens]
    for index in sorted(range(len(term_tokens)), key=lambda k: -len(term_tokens[k])):
        found[index] = _claim(term_tokens[index], tokens, claimed, possessive)
    return found


def _count_terms(term_tokens: list[list[str]], tokens: list[str]) -> list[int]:
    return [len(occurrences) for occurrences in _find_terms(term_tokens, tokens)]


def _term_tokens(terms: list[str]) -> list[list[str]]:
    term_tokens = [tokenize(term, lowercase=False) for term in terms]
    for term, tokens in zip(terms, term_tokens, strict=True):
        if not tokens:
            raise ValueError(f"glossary term {term!r} has no words after normalization")
    return term_tokens


def term_occurrences(text: str, terms: Iterable[str],
                     possessive: bool = False) -> list[list[tuple[int, int]]]:
    """
    Where each term occurs in `text`: per term, the token ranges (start, end
    exclusive) in `tokenize(text, lowercase=False)`. Without `possessive` as
    `term_accuracy_v1` counts them; with it as `term_accuracy_v2` does.
    """
    term_tokens = [tokenize(term, lowercase=False) for term in terms]
    return _find_terms(term_tokens, tokenize(text, lowercase=False), possessive)


def term_accuracy_v1(reference: str, hypothesis: str, terms: Iterable[str]) -> TermAccuracyResult:
    """
    How many of the glossary terms spoken in the reference the hypothesis has.
    Superseded as the benchmark metric by `term_accuracy_v2` on 2026-11-11 and kept
    for the record (the `term_accuracy_v1` column of results/benchmark.csv) and
    for the stages' occurrence checks (`term_accuracy` is this function).

    Each term is tokenized like the texts (`tokenize`, case kept) and counted as a
    whole-token sequence: `npm` does not match inside `pnpm`, and `Claude Code`
    matches only the two tokens in a row. The last token may also be in a regular
    English plural (`plural_forms`): the glossary keeps the base form, and
    `pull requests` counts for `pull request`. Each token of a term is compared
    case-sensitively if it is an identifier (`is_identifier`) and
    case-insensitively otherwise, so `GitHub` must be spelled `GitHub` while
    `subagent` may be `Subagent`.

    Terms that overlap are counted once: longer terms (in tokens) claim their
    tokens first, and a shorter term counts only where no longer term matched.
    With `Claude` and `Claude Code` in the glossary, "Claude Code" counts for
    `Claude Code` only, and a lone "Claude" for `Claude`.

    Per term: `expected` = occurrences in the reference; `found` =
    min(expected, occurrences in the hypothesis). The count is position-free:
    it does not check that the hypothesis has the term at the same place, only
    that it has it as often. The result is the micro-average sum(found) /
    sum(expected) over the terms with expected > 0; terms absent from the
    reference are listed in `absent` and excluded.
    """
    terms = list(terms)
    term_tokens = _term_tokens(terms)
    in_reference = _count_terms(term_tokens, tokenize(reference, lowercase=False))
    in_hypothesis = _count_terms(term_tokens, tokenize(hypothesis, lowercase=False))
    rows, absent = [], []
    counts = zip(terms, term_tokens, in_reference, in_hypothesis, strict=True)
    for term, tokens, expected, hyp_count in counts:
        if expected == 0:
            absent.append(term)
            continue
        rows.append(TermRow(term, any(is_identifier(t) for t in tokens), expected, min(expected, hyp_count)))
    total = sum(row.expected for row in rows)
    accuracy = sum(row.found for row in rows) / total if total else None
    return TermAccuracyResult(rows=rows, absent=absent, accuracy=accuracy)


# The stages (correct.py, translate.py, glossary_auto.py, ref_glossary.py) use the
# v1 function as an occurrence check of a term in a text (reference = hypothesis);
# position does not matter there. The benchmark's term accuracy is v2.
term_accuracy = term_accuracy_v1


# --- Term accuracy v2: position-aware (the benchmark metric since 2026-11-11) ---


@dataclass(frozen=True)
class TermRowV2:
    term: str
    identifier: bool  # at least one token of the term matches case-sensitively
    expected: int  # occurrences in the reference
    found: int  # of those, how many the hypothesis has in the same place (aligned)
    in_hypothesis: int  # occurrences anywhere in the hypothesis
    false_positives: int  # hypothesis occurrences not aligned to a reference occurrence of the term


@dataclass(frozen=True)
class TermAccuracyV2:
    rows: list[TermRowV2]  # terms that occur in the reference, in glossary order
    absent: list[str]  # glossary terms that do not occur in the reference; not in the averages
    accuracy: float | None  # micro-average: sum(found) / sum(expected); None when no term occurs
    macro: float | None  # mean of found / expected over the terms in `rows` (each term weighs the same)
    top_term: str | None  # the term with the most reference occurrences (first in glossary order on a tie)
    micro_without_top: float | None  # micro-average without `top_term`; None with fewer than two terms
    false_positives: int  # hypothesis occurrences of any glossary term (absent ones too) not matched
    hypothesis_occurrences: int  # occurrences of any glossary term in the hypothesis
    precision: float | None  # matched / hypothesis_occurrences; None when the hypothesis has no term
    occurrences: list[list[tuple[int, int]]]  # per glossary term (all, glossary order): reference ranges
    hits: list[list[bool]]  # per glossary term, per reference occurrence: matched in place

    @property
    def expected(self) -> int:
        return sum(row.expected for row in self.rows)

    @property
    def found(self) -> int:
        return sum(row.found for row in self.rows)


def _alignment(reference: Sequence[str], hypothesis: Sequence[str]) -> dict[int, int]:
    """
    Reference token index -> hypothesis token index for the aligned pairs (equal or
    substituted) of the minimum-edit word alignment of the lower-cased tokens, the
    alignment WER uses. Inserted and deleted words have no partner.
    """
    if not reference or not hypothesis:
        return {}
    out = jiwer.process_words(_joined([t.lower() for t in reference]),
                              _joined([t.lower() for t in hypothesis]))
    pairs: dict[int, int] = {}
    for chunk in out.alignments[0]:
        if chunk.type in ("equal", "substitute"):
            for k in range(chunk.ref_end_idx - chunk.ref_start_idx):
                pairs[chunk.ref_start_idx + k] = chunk.hyp_start_idx + k
    return pairs


def term_accuracy_v2(reference: str, hypothesis: str, terms: Iterable[str]) -> TermAccuracyV2:
    """
    Position-aware term accuracy: how many glossary-term occurrences of the
    reference the hypothesis has **in the same place**, and how many term
    occurrences it has that the reference does not.

    Occurrences are found in each text as in `term_accuracy_v1` (whole-token
    sequences, longer terms claim their tokens first, identifiers case-sensitive,
    a regular plural of the last word accepted) **plus possessives**: the last
    word may end in `'s` (`Anthropic's` counts for `Anthropic`).

    The two texts are aligned word by word (`_alignment`: the minimum-edit
    alignment of WER, on lower-cased tokens). A reference occurrence of a term
    with `n` tokens starting at reference token `s` is a **hit** if the reference
    tokens `s .. s+n-1` are aligned to `n` consecutive hypothesis tokens `h ..
    h+n-1` and the hypothesis has an occurrence of the same term exactly there.
    A term written correctly somewhere else does not count, and a term spelled
    with the wrong case, as two words for one, or replaced by another term is a
    miss. Each hypothesis occurrence matches at most one reference occurrence.

    Reported:
    - `accuracy` (micro-average): hits / reference occurrences, over all terms;
    - `macro`: the mean of each term's hits / occurrences, so every term weighs
      the same however often it is said;
    - `micro_without_top`: the micro-average without the most frequent term,
      so one dominant term cannot carry the number;
    - `false_positives`: hypothesis occurrences of a glossary term (including
      terms absent from the reference) that match no reference occurrence in
      place; `precision` = matched / all hypothesis occurrences.
    """
    terms = list(terms)
    term_tokens = _term_tokens(terms)
    ref = tokenize(reference, lowercase=False)
    hyp = tokenize(hypothesis, lowercase=False)
    ref_found = _find_terms(term_tokens, ref, possessive=True)
    hyp_found = _find_terms(term_tokens, hyp, possessive=True)
    pairs = _alignment(ref, hyp)
    rows, absent, hits_all = [], [], []
    matched_total = hyp_total = 0
    for term, tokens, in_ref, in_hyp in zip(terms, term_tokens, ref_found, hyp_found, strict=True):
        free = set(in_hyp)
        hits = []
        for start, end in in_ref:
            h = pairs.get(start)
            n = end - start
            hit = (h is not None and all(pairs.get(start + k) == h + k for k in range(n))
                   and (h, h + n) in free)
            if hit:
                free.discard((h, h + n))
            hits.append(hit)
        hits_all.append(hits)
        matched = len(in_hyp) - len(free)
        matched_total += matched
        hyp_total += len(in_hyp)
        if not in_ref:
            absent.append(term)
            continue
        rows.append(TermRowV2(term, any(is_identifier(t) for t in tokens), len(in_ref), sum(hits),
                              len(in_hyp), len(free)))
    total = sum(r.expected for r in rows)
    accuracy = sum(r.found for r in rows) / total if total else None
    macro = sum(r.found / r.expected for r in rows) / len(rows) if rows else None
    top = max(rows, key=lambda r: r.expected) if rows else None  # max keeps the first on a tie
    rest = [r for r in rows if r is not top]
    rest_total = sum(r.expected for r in rest)
    without_top = sum(r.found for r in rest) / rest_total if rest_total else None
    false_positives = hyp_total - matched_total
    return TermAccuracyV2(
        rows=rows, absent=absent, accuracy=accuracy, macro=macro,
        top_term=top.term if top else None, micro_without_top=without_top,
        false_positives=false_positives, hypothesis_occurrences=hyp_total,
        precision=matched_total / hyp_total if hyp_total else None,
        occurrences=ref_found, hits=hits_all,
    )


# --- Confidence interval -------------------------------------------------------

Z_95 = 1.959963984540054


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """
    Wilson score interval (95% by default) for a proportion of `successes` out of
    `n`. Raises ValueError for n <= 0 or successes outside 0..n.
    """
    if n <= 0 or not 0 <= successes <= n:
        raise ValueError(f"need 0 <= successes <= n and n > 0, got {successes} of {n}")
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


# --- Term accuracy with bounds (the T6 gate) --------------------------------

TERM_ACCURACY_GATE = 0.85  # stage 2 gate (CLAUDE.md, Stage gates)
OVERFITTING_MARGIN = 0.10  # T6 more than 10 points below the T2 mean (docs/experiments.md, T6 protocol)

PASS_DEMONSTRATED = "pass (demonstrated)"
PASS_NOT_DEMONSTRATED = "pass (not demonstrated)"
FAIL = "fail"
INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class BoundedTermAccuracy:
    against_j: TermAccuracyV2  # reference J: the judge's text in every judged span
    against_m: TermAccuracyV2  # reference M: the engines' majority where one exists
    lower: float  # uncertain occurrences counted as misses
    upper: float  # uncertain occurrences counted as hits
    occurrences: int  # glossary-term occurrences in J (the denominator of J, lower and upper)
    uncertain_occurrences: int  # of those, the ones inside an uncertain span
    certain_hits: int  # hits outside the uncertain spans (the numerator of `lower`)
    threshold: float
    verdict: str  # PASS_DEMONSTRATED | PASS_NOT_DEMONSTRATED | FAIL | INCONCLUSIVE
    interval_j: tuple[float, float]  # Wilson 95% intervals
    interval_m: tuple[float, float]
    interval_lower: tuple[float, float]
    interval_upper: tuple[float, float]


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def gate_verdict(lower: float, upper: float, lower_interval_low: float,
                 threshold: float = TERM_ACCURACY_GATE) -> str:
    """
    The T6 verdict (docs/experiments.md, T6 protocol): pass (demonstrated) if the
    Wilson lower limit of the lower bound >= threshold; pass (not demonstrated)
    if the lower bound >= threshold but its Wilson lower limit is below; fail if
    the upper bound < threshold; otherwise inconclusive.
    """
    if lower >= threshold:
        return PASS_DEMONSTRATED if lower_interval_low >= threshold else PASS_NOT_DEMONSTRATED
    return FAIL if upper < threshold else INCONCLUSIVE


def overfitting_signal(t6_accuracy: float, t2_accuracies: Sequence[float],
                       margin: float = OVERFITTING_MARGIN) -> bool:
    """True if `t6_accuracy` is more than `margin` below the mean of `t2_accuracies`."""
    if not t2_accuracies:
        raise ValueError("no T2 values to compare with")
    return sum(t2_accuracies) / len(t2_accuracies) - t6_accuracy > margin


def bounded_term_accuracy(reference_j: str, reference_m: str, hypothesis: str, terms: Iterable[str],
                          uncertain: Iterable[tuple[int, int]],
                          threshold: float = TERM_ACCURACY_GATE) -> BoundedTermAccuracy:
    """
    Term accuracy v2 when part of the reference is uncertain (the T6 gate).

    `uncertain` are token ranges (start, end exclusive) in
    `tokenize(reference_j, lowercase=False)`: the spans where the reference is
    one judge's decision alone or where two ways of building it disagree. A
    glossary-term occurrence of J (`term_accuracy_v2`, possessives included) is
    uncertain if its tokens overlap such a range (an empty range overlaps
    nothing).

    Every certain occurrence is scored as `term_accuracy_v2` scores it (a hit
    only in place). Then

        lower = certain hits / occurrences                      (uncertain = misses)
        upper = (certain hits + uncertain occurrences) / occurrences   (uncertain = hits)

    so lower <= accuracy against J <= upper. Each of J, M, lower and upper gets
    a Wilson 95% interval over its own occurrences; the verdict is
    `gate_verdict`. The accuracy against M (same terms; terms absent from M are
    excluded there) is reported, not used by the verdict.
    """
    terms = list(terms)
    ranges = list(uncertain)
    against_j = term_accuracy_v2(reference_j, hypothesis, terms)
    against_m = term_accuracy_v2(reference_m, hypothesis, terms)
    expected = uncertain_count = certain_hits = 0
    for found, hits in zip(against_j.occurrences, against_j.hits, strict=True):
        for occurrence, hit in zip(found, hits, strict=True):
            expected += 1
            if any(_overlaps(occurrence, r) for r in ranges):
                uncertain_count += 1
            elif hit:
                certain_hits += 1
    if not expected:
        raise ValueError("no glossary term occurs in reference J; the bounds are undefined")
    lower = certain_hits / expected
    upper = (certain_hits + uncertain_count) / expected
    interval_lower = wilson_interval(certain_hits, expected)
    m_expected = against_m.expected
    return BoundedTermAccuracy(
        against_j=against_j, against_m=against_m, lower=lower, upper=upper, occurrences=expected,
        uncertain_occurrences=uncertain_count, certain_hits=certain_hits, threshold=threshold,
        verdict=gate_verdict(lower, upper, interval_lower[0], threshold),
        interval_j=wilson_interval(against_j.found, expected),
        interval_m=wilson_interval(against_m.found, m_expected) if m_expected else (0.0, 1.0),
        interval_lower=interval_lower,
        interval_upper=wilson_interval(certain_hits + uncertain_count, expected),
    )


# --- False-edit rate -------------------------------------------------------
#
# One definition (docs/experiments.md, Metrics): the share of the correction's
# word edits that move the text further from the reference. `false_edit_rate`
# is it; `false_edit_rate_v1` is the earlier measure, kept for the record.


@dataclass(frozen=True)
class FalseEditResult:
    edits: int  # word edits of the correction relative to its input: substitutions + deletions + insertions
    false_edits: int  # edits that, applied alone to the input, increase its word errors against the reference
    helpful_edits: int  # edits that, applied alone, decrease them (the rest change nothing measurable)
    rate: float | None  # false_edits / edits; None when the correction changed no word


def _word_errors(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    if not hypothesis:
        return len(reference)
    out = jiwer.process_words(_joined(reference), _joined(hypothesis))
    return out.substitutions + out.deletions + out.insertions


def correction_edits(raw: Sequence[str], fixed: Sequence[str]) -> list[tuple[str, int, str | None]]:
    """
    The word edits that turn `raw` into `fixed`, from their minimum-edit word
    alignment: `("sub", i, word)` replaces raw token i, `("del", i, None)` deletes
    it, `("ins", i, word)` inserts a word before raw token i (i = len(raw) at the end).
    """
    if not raw:
        return [("ins", 0, word) for word in fixed]
    if not fixed:
        return [("del", i, None) for i in range(len(raw))]
    out = jiwer.process_words(_joined(raw), _joined(fixed))
    edits: list[tuple[str, int, str | None]] = []
    for chunk in out.alignments[0]:
        if chunk.type == "substitute":
            edits += [("sub", chunk.ref_start_idx + k, fixed[chunk.hyp_start_idx + k])
                      for k in range(chunk.ref_end_idx - chunk.ref_start_idx)]
        elif chunk.type == "delete":
            edits += [("del", i, None) for i in range(chunk.ref_start_idx, chunk.ref_end_idx)]
        elif chunk.type == "insert":
            edits += [("ins", chunk.ref_start_idx, fixed[j])
                      for j in range(chunk.hyp_start_idx, chunk.hyp_end_idx)]
    return edits


def _apply(raw: Sequence[str], edit: tuple[str, int, str | None]) -> list[str]:
    kind, i, word = edit
    tokens = list(raw)
    if kind == "sub":
        tokens[i] = word
    elif kind == "del":
        del tokens[i]
    else:
        tokens.insert(i, word)
    return tokens


def false_edit_rate(raw: Sequence[str], fixed: Sequence[str], reference: Sequence[str]) -> FalseEditResult:
    """
    False-edit rate: the share of the correction's word edits that make the text
    further from the reference.

    Inputs are token lists (normally `tokenize(text)` of the correction's input,
    its output and the reference). The edits are the word substitutions,
    deletions and **insertions** of the minimum-edit alignment of input -> output
    (`correction_edits`); each word counts as one edit. An edit is **false** if
    applying it alone to the input increases the input's word errors against the
    reference (substitutions + deletions + insertions, as in WER); it is helpful
    if it decreases them. Rate = false edits / edits; None when nothing changed.
    An invented word inserted by the correction is a false edit.

    Approximation: each edit is judged alone, so edits that only help together
    (rare: a word split across two substitutions) are judged one by one, and the
    alignment is one of several equally short ones.
    """
    ref = list(reference)
    if not ref:
        raise ValueError("the reference has no words")
    raw = list(raw)
    edits = correction_edits(raw, list(fixed))
    base = _word_errors(ref, raw)
    false = helpful = 0
    for edit in edits:
        errors = _word_errors(ref, _apply(raw, edit))
        false += errors > base
        helpful += errors < base
    return FalseEditResult(edits=len(edits), false_edits=false, helpful_edits=helpful,
                           rate=false / len(edits) if edits else None)


@dataclass(frozen=True)
class FalseEditResultV1:
    false_edits: int
    raw_hits: int
    rate: float | None  # false_edits / raw_hits; None when the raw text has no hits


def _hit_positions(reference: Sequence[str], hypothesis: Sequence[str]) -> set[int]:
    if not hypothesis:
        return set()
    out = jiwer.process_words(_joined(reference), _joined(hypothesis))
    hits = set()
    for chunk in out.alignments[0]:
        if chunk.type == "equal":
            hits.update(range(chunk.ref_start_idx, chunk.ref_end_idx))
    return hits


def false_edit_rate_v1(raw: Sequence[str], fixed: Sequence[str],
                       reference: Sequence[str]) -> FalseEditResultV1:
    """
    Superseded 2026-11-11 by `false_edit_rate`; kept for the record (the
    `false_edit_rate_v1` column of results/benchmark.csv). Not a share of edits:
    the share of the reference words the raw transcript got right that the
    correction broke (raw hits no longer aligned as equal after correction).
    Inserted words never count, so a correction that invents words scores 0.
    """
    ref = list(reference)
    if not ref:
        raise ValueError("the reference has no words")
    raw_hits = _hit_positions(ref, raw)
    fixed_hits = _hit_positions(ref, fixed)
    false_edits = len(raw_hits - fixed_hits)
    return FalseEditResultV1(
        false_edits=false_edits,
        raw_hits=len(raw_hits),
        rate=false_edits / len(raw_hits) if raw_hits else None,
    )


# --- Timing drift ----------------------------------------------------------


def timing_drift(hyp_segments, ref_segments) -> tuple[float, float]:
    """
    How far subtitle timings are from the speech they belong to: (median_ms,
    p95_ms) of the absolute start-time differences between matched segments.
    Needs reference timestamps labelled by hand, which do not exist yet.
    """
    raise NotImplementedError("stage 1, needs hand-labelled timestamps")


# --- Reading speed ---------------------------------------------------------

DEFAULT_MAX_CPS = 17.0


@dataclass(frozen=True)
class ReadingSpeedResult:
    cps: list[float]  # characters per second, one per cue, in input order
    threshold: float
    share_above: float  # share of cues with cps strictly above the threshold
    max_cps: float


def reading_speed(cues: Iterable[tuple[str, float, float]],
                  threshold: float = DEFAULT_MAX_CPS) -> ReadingSpeedResult:
    """
    Characters per second a viewer must read, per subtitle cue `(text, start, end)`
    with times in seconds.

    Characters = the cue text with line breaks and runs of whitespace collapsed
    to single spaces and the ends trimmed; spaces and punctuation count, as in
    common subtitle guidelines. cps = characters / (end - start). `share_above` is
    the share of cues strictly above `threshold` (17 cps by default). Raises
    ValueError for a cue that does not last a positive time, or for no cues.
    """
    speeds = []
    for number, (text, start, end) in enumerate(cues, start=1):
        duration = end - start
        if duration <= 0:
            raise ValueError(f"cue {number}: duration {duration:.3f} s is not positive")
        speeds.append(len(" ".join(text.split())) / duration)
    if not speeds:
        raise ValueError("no cues")
    return ReadingSpeedResult(
        cps=speeds,
        threshold=threshold,
        share_above=sum(cps > threshold for cps in speeds) / len(speeds),
        max_cps=max(speeds),
    )
