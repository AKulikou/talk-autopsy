"""
Metrics for the benchmark. Pure functions: text and numbers in, numbers out, no
file or console I/O (the `eval` command in cli.py reads the files).

Every number in results/benchmark.csv and the README comes from here, so each
function says exactly what it measures and how its input is normalized. Rates
are returned as fractions (0.123), not percentages.

Text normalization (shared by WER, term accuracy and false-edit rate) is
`tokenize`: annotation markers such as `[inaudible]` are removed, punctuation is
stripped from word edges but kept inside identifiers, whitespace is collapsed,
and nothing is expanded (no numbers to words, no contractions, no spelling
variants). WER lower-cases every token; term accuracy keeps the case so that
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
    5. With `lowercase` (the default, used by WER) tokens are lower-cased.

    Nothing is expanded or merged: `10` and `ten`, `open-source` and
    `open source`, `don't` and `do not` are different tokens.
    """
    prepared = _PREPARE(text)
    tokens = []
    for chunk in prepared.split():
        token = _strip_edges(chunk)
        if any(ch.isalnum() for ch in token):
            tokens.append(token.lower() if lowercase else token)
    return tokens


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


def _claim(term_tokens: list[str], tokens: list[str], claimed: list[bool]) -> int:
    """
    Count non-overlapping occurrences of `term_tokens` in `tokens` that use no
    position claimed by another term, and claim the positions they use.
    """
    count, i, n = 0, 0, len(term_tokens)
    while i + n <= len(tokens):
        window = range(i, i + n)
        if not any(claimed[j] for j in window) and all(
            _token_matches(t, tokens[j]) for t, j in zip(term_tokens, window, strict=True)
        ):
            for j in window:
                claimed[j] = True
            count += 1
            i += n
        else:
            i += 1
    return count


def _count_terms(term_tokens: list[list[str]], tokens: list[str]) -> list[int]:
    """Occurrences of each term, longest terms first, so a term inside a longer one is not counted twice."""
    claimed = [False] * len(tokens)
    counts = [0] * len(term_tokens)
    for index in sorted(range(len(term_tokens)), key=lambda k: -len(term_tokens[k])):
        counts[index] = _claim(term_tokens[index], tokens, claimed)
    return counts


def term_accuracy(reference: str, hypothesis: str, terms: Iterable[str]) -> TermAccuracyResult:
    """
    How many of the glossary terms spoken in the reference the hypothesis has.

    Each term is tokenized like the texts (`tokenize`, case kept) and counted as a
    whole-token sequence: `npm` does not match inside `pnpm`, and `Claude Code`
    matches only the two tokens in a row. Each token of a term is compared
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
    term_tokens = [tokenize(term, lowercase=False) for term in terms]
    for term, tokens in zip(terms, term_tokens, strict=True):
        if not tokens:
            raise ValueError(f"glossary term {term!r} has no words after normalization")
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


# --- False-edit rate -------------------------------------------------------


@dataclass(frozen=True)
class FalseEditResult:
    false_edits: int
    raw_hits: int
    rate: float | None  # false_edits / raw_hits; None when the raw text has no hits


def _hit_positions(reference: Sequence[str], hypothesis: Sequence[str]) -> set[int]:
    out = jiwer.process_words(_joined(reference), _joined(hypothesis))
    hits = set()
    for chunk in out.alignments[0]:
        if chunk.type == "equal":
            hits.update(range(chunk.ref_start_idx, chunk.ref_end_idx))
    return hits


def false_edit_rate(raw: Sequence[str], fixed: Sequence[str], reference: Sequence[str]) -> FalseEditResult:
    """
    Share of the words speech recognition got right that the correction broke.

    Inputs are token lists (normally `tokenize(text)` of the raw transcript, the
    corrected transcript and the reference). raw -> reference and fixed ->
    reference are each aligned with `jiwer.process_words`. A reference position
    is a raw hit if the raw alignment marks it equal; it is a false edit if it is
    a raw hit and the fixed alignment does not mark it equal. Rate = false edits
    / raw hits.

    This is an approximation based on word alignment: the minimum-edit alignment
    is one of several equally short ones, so a correct word next to a changed one
    can occasionally be attributed differently, and an edit that moves a correct
    word to another place counts as breaking it.
    """
    ref = list(reference)
    if not ref:
        raise ValueError("the reference has no words")
    raw_hits = _hit_positions(ref, raw)
    fixed_hits = _hit_positions(ref, fixed)
    false_edits = len(raw_hits - fixed_hits)
    return FalseEditResult(
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
