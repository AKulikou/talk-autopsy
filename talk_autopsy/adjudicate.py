"""
Reference transcripts by consensus of three engines plus human adjudication of
their disagreements only.

    backbone (faster-whisper, word timings) + two cloud transcripts
      -> alignment -> spans of disagreement -> adjudication file (+ audio clips)
      -> owner ticks one variant per span -> resolve -> T<n>.ref.txt + protocol

Pure functions, except `write_clips` (ffmpeg); the CLI reads and writes the
files.

**Tokens.** Each transcript is split into words (the backbone's timed words;
the cloud transcripts' segment texts split on whitespace), and every word is
normalized with `metrics.tokenize`, the one normalization of the benchmark. A
word that normalizes to nothing (a filler such as `um`, or punctuation) is
dropped. A word that normalizes to one token keeps its original spelling for
display; one that splits into several (`well—so`) is shown token by token.

**Alignment.** Matching uses the lower-cased tokens. Each cloud transcript is
aligned to the backbone with `difflib.SequenceMatcher` (autojunk off). The
backbone has positions 0..n-1 and insertion slots 0..n (slot k lies before
position k). A position disagrees if any cloud transcript does not match it; a
slot disagrees if a cloud transcript inserts there. A replaced or deleted block
also marks the slots inside it, so every non-matching block lies whole inside
one run of disagreement.

**Spans.** Runs of disagreement separated by fewer than `MERGE_GAP` (3) agreeing
backbone words are merged. Each engine's variant of a span is its text over the
span. Variants are grouped by their lower-cased tokens: three groups = `split`
(nothing pre-marked), two = `majority` (the larger group pre-marked), one =
`unanimous` (possible when the alignment marks equal text as changed; not
listed, counted as agreement).

**Spelling where engines agree.** The backbone's spelling (with its punctuation)
is used, but with the letter case most engines use (`Claude Code` over the
backbone's `Claude code`, `GitHub` over `Github`); on a three-way tie the
backbone's case stays.

**Times.** A span's clip runs from its first backbone word's start to its last
word's end, padded by `PAD_SECONDS` each side and clipped to the audio. Gemini
timestamps are never used; only the backbone's measured word times are.
"""

import difflib
import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from talk_autopsy import metrics
from talk_autopsy.segments import Transcript

MERGE_GAP = 3  # spans separated by fewer agreeing words are merged
PAD_SECONDS = 2.5
PARAGRAPH_PAUSE_SECONDS = 2.0
CONTEXT_WORDS = 5
NO_WORDS = "(no words)"  # an engine has nothing in the span
LABELS = "ABC"

DOT = chr(0xB7)  # middle dot in the block header
DASH = chr(0x2013)  # en dash between times
ELLIPSIS = chr(0x2026)


class AdjudicationError(ValueError):
    """The inputs or the adjudication file do not allow a consensus or a reference."""


@dataclass(frozen=True)
class Token:
    norm: str  # lower-cased token, used for matching
    cased: str  # token with its case kept
    display: str  # original spelling, punctuation included
    start: float | None = None  # backbone only
    end: float | None = None


def _tokens_of_word(text: str, start: float | None = None, end: float | None = None) -> list[Token]:
    cased = metrics.tokenize(text, lowercase=False)
    if len(cased) == 1:
        return [Token(cased[0].lower(), cased[0], text.strip(), start, end)]
    return [Token(c.lower(), c, c, start, end) for c in cased]


def rejoin_words(segment) -> list[tuple[str, float, float]]:
    """
    The segment's words as written in its text, with timings. faster-whisper
    splits some written words into pieces (`up` + `-level`, `Q` + `&A.`) whose
    spaces it drops; the pieces are joined back to each whitespace-separated word
    of the segment text, from the first piece's start to the last one's end. If
    the pieces do not rebuild the text exactly, the words are used as they are.
    """
    words = segment.words or []
    as_is = [(w.text.strip(), w.start, w.end) for w in words]
    joined, i = [], 0
    for chunk in segment.text.split():
        if i >= len(words):
            return as_is
        text, start, end = words[i].text.strip(), words[i].start, words[i].end
        i += 1
        while text != chunk and i < len(words) and chunk.startswith(text):
            text, end = text + words[i].text.strip(), words[i].end
            i += 1
        if text != chunk:
            return as_is
        joined.append((text, start, end))
    return joined if i == len(words) else as_is


def backbone_tokens(transcript: Transcript) -> list[Token]:
    """Timed tokens of the backbone transcript; every segment must have word timings."""
    tokens: list[Token] = []
    for segment in transcript.segments:
        if not segment.words:
            if metrics.tokenize(segment.text):
                raise AdjudicationError(f"backbone segment {segment.id} has text but no word timings; "
                                        "the backbone must be a transcript with words (faster-whisper)")
            continue
        for text, start, end in rejoin_words(segment):
            tokens.extend(_tokens_of_word(text, start, end))
    if not tokens:
        raise AdjudicationError("the backbone has no words")
    return tokens


def text_tokens(transcript: Transcript) -> list[Token]:
    """Untimed tokens of a transcript, from its segment texts."""
    return [t for segment in transcript.segments for chunk in segment.text.split()
            for t in _tokens_of_word(chunk)]


# --- alignment -------------------------------------------------------------


@dataclass
class Alignment:
    """One cloud transcript against the backbone."""

    flags: list[bool]  # per linear index (slot k = 2k, position i = 2i+1): disagreement
    assigned: dict[int, list[Token]]  # linear index -> this transcript's tokens placed there


def align(backbone: list[Token], hypothesis: list[Token]) -> Alignment:
    n = len(backbone)
    flags = [False] * (2 * n + 1)
    assigned: dict[int, list[Token]] = {}
    matcher = difflib.SequenceMatcher(None, [t.norm for t in backbone], [t.norm for t in hypothesis],
                                      autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                assigned.setdefault(2 * (i1 + offset) + 1, []).append(hypothesis[j1 + offset])
            continue
        if tag == "insert":
            flags[2 * i1] = True
            assigned.setdefault(2 * i1, []).extend(hypothesis[j1:j2])
            continue
        # replace or delete: the whole block, positions and the slots inside it
        for index in range(2 * i1 + 1, 2 * i2):
            flags[index] = True
        if j2 > j1:
            assigned.setdefault(2 * i1 + 1, []).extend(hypothesis[j1:j2])
    return Alignment(flags, assigned)


# --- spans -----------------------------------------------------------------


@dataclass
class Option:
    label: str  # "A", "A+B", ...
    engines: list[str]
    text: str  # display text, NO_WORDS when empty
    words: int  # tokens in the variant


@dataclass
class Span:
    first: int  # linear index range, inclusive
    last: int
    verdict: str = ""  # unanimous | majority | split
    options: list[Option] = field(default_factory=list)
    premarked: int | None = None  # index into options (majority only)
    number: int | None = None  # 1, 2, ... for listed spans
    start: float = 0.0  # clip range, seconds
    end: float = 0.0
    speech_start: float = 0.0  # backbone speech in the span (or the gap of an insertion)
    speech_end: float = 0.0
    context_before: str = ""
    context_after: str = ""

    def positions(self) -> range:
        """Backbone positions inside the span."""
        return range(self.first // 2, (self.last + 1) // 2)


def find_runs(flags: list[bool]) -> list[tuple[int, int]]:
    runs, start = [], None
    for index, flag in enumerate(flags):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            runs.append((start, index - 1))
            start = None
    if start is not None:
        runs.append((start, len(flags) - 1))
    return runs


def merge_runs(runs: list[tuple[int, int]], gap: int = MERGE_GAP) -> list[tuple[int, int]]:
    """Merge runs separated by fewer than `gap` backbone positions (odd linear indices)."""
    merged: list[tuple[int, int]] = []
    for first, last in runs:
        if merged:
            between = sum(1 for index in range(merged[-1][1] + 1, first) if index % 2 == 1)
            if between < gap:
                merged[-1] = (merged[-1][0], last)
                continue
        merged.append((first, last))
    return merged


def _join(tokens: list[Token]) -> str:
    return " ".join(t.display for t in tokens)


def _group_variants(engines: list[str], variants: list[list[Token]]) -> list[Option]:
    groups: dict[tuple[str, ...], list[int]] = {}
    for index, tokens in enumerate(variants):
        groups.setdefault(tuple(t.norm for t in tokens), []).append(index)
    options = []
    for members in groups.values():
        tokens = variants[members[0]]
        options.append(Option(label="+".join(LABELS[m] for m in members),
                              engines=[engines[m] for m in members],
                              text=_join(tokens) or NO_WORDS, words=len(tokens)))
    return options


def _agreed_display(index: int, backbone: list[Token], alignments: list[Alignment]) -> str:
    """Backbone spelling of an agreed word, with the letter case most engines use."""
    token = backbone[index]
    forms = [token.cased] + [a.assigned[2 * index + 1][0].cased for a in alignments]
    if len(set(forms)) == 1:
        return token.display
    winner = Counter(forms).most_common(1)[0]
    if winner[1] == 1 or winner[0] == token.cased:  # a tie keeps the backbone's form
        return token.display
    return token.display.replace(token.cased, winner[0], 1)


@dataclass
class Consensus:
    fragment: str
    engines: list[str]  # A, B, C
    audio_seconds: float
    words: int  # backbone tokens
    pieces: list[dict]  # {"word": text, "pos": i} or {"span": index into spans}
    spans: list[Span]
    paragraph_breaks: list[int]  # backbone positions that start a paragraph

    @property
    def listed(self) -> list[Span]:
        return [s for s in self.spans if s.number is not None]

    def counts(self) -> dict:
        listed = self.listed
        in_spans = sum(len(s.positions()) for s in listed)
        return {
            "words": self.words,
            "unanimous_words": self.words - in_spans,
            "unanimous_share": round((self.words - in_spans) / self.words, 4),
            "majority_spans": sum(1 for s in listed if s.verdict == "majority"),
            "split_spans": sum(1 for s in listed if s.verdict == "split"),
            "unanimous_spans": sum(1 for s in self.spans if s.verdict == "unanimous"),
            "clip_minutes": round(sum(s.end - s.start for s in listed) / 60, 1),
        }


def build_consensus(fragment: str, backbone: Transcript, hypotheses: list[Transcript],
                    audio_seconds: float, engines: list[str] | None = None) -> Consensus:
    """Align two transcripts to the backbone and build the spans, pieces and paragraph breaks."""
    if len(hypotheses) != 2:
        raise AdjudicationError(f"expected two hypotheses, got {len(hypotheses)}")
    engines = engines or [str(t.meta.get("engine", t.source)) for t in (backbone, *hypotheses)]
    bb = backbone_tokens(backbone)
    hyps = [text_tokens(h) for h in hypotheses]
    alignments = [align(bb, h) for h in hyps]
    n = len(bb)
    flags = [any(a.flags[i] for a in alignments) for i in range(2 * n + 1)]

    spans: list[Span] = []
    for first, last in merge_runs(find_runs(flags)):
        span = Span(first, last)
        bb_tokens = [bb[p] for p in span.positions()]
        variants = [bb_tokens] + [[t for index in range(first, last + 1) for t in a.assigned.get(index, [])]
                                  for a in alignments]  # each engine's text over the span
        span.options = _group_variants(engines, variants)
        span.verdict = {1: "unanimous", 2: "majority", 3: "split"}[len(span.options)]
        if span.verdict == "majority":
            span.premarked = max(range(2), key=lambda k: len(span.options[k].engines))
        positions = span.positions()
        if positions:
            span.speech_start, span.speech_end = bb[positions[0]].start, bb[positions[-1]].end
        else:  # an insertion between two backbone words
            k = first // 2
            span.speech_start = bb[k - 1].end if k > 0 else 0.0
            span.speech_end = bb[k].start if k < n else audio_seconds
        span.start = max(0.0, span.speech_start - PAD_SECONDS)
        span.end = min(audio_seconds, span.speech_end + PAD_SECONDS)
        before = range(max(0, positions.start - CONTEXT_WORDS), positions.start)
        after = range(positions.stop, min(n, positions.stop + CONTEXT_WORDS))
        span.context_before = " ".join(bb[p].display for p in before)
        span.context_after = " ".join(bb[p].display for p in after)
        spans.append(span)

    number = 0
    for span in spans:
        if span.verdict != "unanimous":
            number += 1
            span.number = number

    pieces: list[dict] = []
    span_at = {s.first: index for index, s in enumerate(spans)}
    covered = {p for s in spans for p in s.positions()}
    index = 0
    while index <= 2 * n:
        if index in span_at:
            span = spans[span_at[index]]
            pieces.append({"span": span_at[index]})
            index = span.last + 1
            continue
        if index % 2 == 1 and index // 2 not in covered:
            pos = index // 2
            pieces.append({"word": _agreed_display(pos, bb, alignments), "pos": pos})
        index += 1

    breaks = [p for p in range(1, n) if bb[p].start - bb[p - 1].end > PARAGRAPH_PAUSE_SECONDS]
    return Consensus(fragment, engines, audio_seconds, n, pieces, spans, breaks)


# --- the adjudication file -------------------------------------------------


def mmss(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def clip_name(span: Span) -> str:
    return f"{span.number:04d}_{mmss(span.start).replace(':', '-')}.wav"


def adjudication_markdown(consensus: Consensus, clip_dir: str, link_prefix: str = "") -> str:
    """
    The file the owner edits. `clip_dir` is the clip folder as shown (e.g.
    work/T2/clips); `link_prefix` makes the link relative to the file's folder.
    """
    c = consensus.counts()
    labels = ", ".join(f"{LABELS[i]} = {e}" for i, e in enumerate(consensus.engines))
    lines = [
        f"# {consensus.fragment} adjudication",
        "",
        f"Engines: {labels}. Backbone: {consensus.engines[0]} (measured word timings).",
        "",
        f"- words (backbone): {c['words']}",
        f"- unanimous: {c['unanimous_words']} words ({c['unanimous_share'] * 100:.1f}%)",
        f"- majority spans: {c['majority_spans']} (pre-marked with the majority variant)",
        f"- split spans: {c['split_spans']} (nothing pre-marked)",
        f"- clip audio: {c['clip_minutes']} minutes",
        "",
        "For every block: listen to the clip, tick exactly one option (`[x]`), or type the",
        "right text after `other:` (a non-empty `other:` wins over any ticked box). Fillers",
        "(uh, um, er, ah, hmm) are not written; see the reference conventions in",
        "data/test-set/README.md. Then run `talk-autopsy resolve "
        f"{consensus.fragment}`.",
        "",
    ]
    for span in consensus.listed:
        clip = f"{clip_dir}/{clip_name(span)}"
        lines += [
            f"### {span.number:04d} {DOT} {mmss(span.start)}{DASH}{mmss(span.end)} {DOT} {span.verdict}",
            f"Clip: [{clip}]({link_prefix}{clip})",
            f"Context: {ELLIPSIS}{span.context_before} [SPAN] {span.context_after}{ELLIPSIS}",
        ]
        for index, option in enumerate(span.options):
            mark = "x" if index == span.premarked else " "
            lines.append(f"- [{mark}] {option.label} ({', '.join(option.engines)}): {option.text}")
        lines += ["- [ ] other: ", ""]
    return "\n".join(lines)


HEADER = re.compile(r"^### (\d{4}) ")
OPTION = re.compile(r"^- \[([ xX])\] (other|[A-C](?:\+[A-C])*)(?: \([^)]*\))?:(.*)$")


@dataclass(frozen=True)
class Choice:
    checked: list[str]  # option labels ticked
    other: str  # text typed after other:, stripped


def parse_choices(text: str) -> dict[int, Choice]:
    """Span number -> what the owner ticked or typed."""
    choices: dict[int, Choice] = {}
    current: int | None = None
    for line in text.splitlines():
        header = HEADER.match(line)
        if header:
            current = int(header.group(1))
            if current in choices:
                raise AdjudicationError(f"span {current:04d} appears twice in the adjudication file")
            choices[current] = Choice([], "")
            continue
        option = OPTION.match(line.rstrip())
        if option and current is not None:
            mark, label, rest = option.groups()
            choice = choices[current]
            if label == "other":
                choices[current] = Choice(choice.checked, rest.strip())
            elif mark in "xX":
                choice.checked.append(label)
    return choices


# --- resolve ---------------------------------------------------------------


@dataclass
class Resolution:
    reference: str
    protocol: dict


def resolve(consensus: Consensus, choices: dict[int, Choice],
            postprocess: Callable[[str], str] | None = None) -> Resolution:
    """
    Compose the reference from the agreed words and the chosen variant of every
    listed span; unanimous spans use their one variant. Errors: a listed span
    missing from the file, nothing chosen, or more than one box ticked without an
    `other:` text. `postprocess` (e.g. `judge.normalize_for_adjudication`, for a
    reference built from a judged file) is applied to every listed span's chosen
    text; the protocol keeps where the text came from.
    """
    chosen_text: dict[int, str] = {}
    span_rows = []
    totals = {"consensus_words": 0, "typed_words": 0, "span_words": 0,
              "words_by_engine": {e: 0 for e in consensus.engines},
              "spans_by_engine": {e: 0 for e in consensus.engines},
              "spans_typed": 0, "majority_kept": 0, "majority_changed": 0}
    problems = []
    for index, span in enumerate(consensus.spans):
        if span.number is None:  # unanimous: the one variant
            option = span.options[0]
            chosen_text[index] = "" if option.text == NO_WORDS else option.text
            totals["consensus_words"] += option.words
            continue
        choice = choices.get(span.number)
        if choice is None:
            problems.append(f"{span.number:04d}: missing from the adjudication file")
            continue
        if choice.other:  # `other: (no words)` types an empty variant
            text = "" if choice.other == NO_WORDS else choice.other
            source, words = "other", len(metrics.tokenize(text))
            totals["typed_words"] += words
            totals["spans_typed"] += 1
        else:
            by_label = {o.label: o for o in span.options}
            unknown = [label for label in choice.checked if label not in by_label]
            if unknown:
                problems.append(f"{span.number:04d}: unknown option {', '.join(unknown)}")
                continue
            if len(choice.checked) != 1:
                what = "nothing chosen" if not choice.checked else f"{len(choice.checked)} options ticked"
                problems.append(f"{span.number:04d} ({span.verdict}): {what}")
                continue
            option = by_label[choice.checked[0]]
            source, words = option.label, option.words
            text = "" if option.text == NO_WORDS else option.text
            for engine in option.engines:
                totals["words_by_engine"][engine] += words
                totals["spans_by_engine"][engine] += 1
        if postprocess is not None:
            text = postprocess(text)
            words = len(metrics.tokenize(text))
        totals["span_words"] += words
        if span.verdict == "majority":
            kept = source == span.options[span.premarked].label
            totals["majority_kept" if kept else "majority_changed"] += 1
        chosen_text[index] = text
        span_rows.append({"number": span.number, "verdict": span.verdict, "time": mmss(span.speech_start),
                          "chosen": source, "words": words, "text": text})
    if problems:
        raise AdjudicationError("the adjudication is not complete:\n  " + "\n  ".join(problems))

    totals["consensus_words"] += sum(1 for piece in consensus.pieces if "word" in piece)
    breaks = set(consensus.paragraph_breaks)
    paragraphs: list[list[str]] = [[]]
    for piece in consensus.pieces:
        if "word" in piece:
            pos, text = piece["pos"], piece["word"]
        else:
            span = consensus.spans[piece["span"]]
            positions = span.positions()
            pos = positions.start if positions else None
            text = chosen_text[piece["span"]]
        if pos in breaks and paragraphs[-1]:
            paragraphs.append([])
        if text:
            paragraphs[-1].append(text)
    reference = "\n\n".join(" ".join(p) for p in paragraphs if p) + "\n"
    totals["reference_words"] = len(metrics.tokenize(reference))
    protocol = {"fragment": consensus.fragment, "engines": consensus.engines, "counts": consensus.counts(),
                "totals": totals, "spans": span_rows,
                "note": "words_by_engine and spans_by_engine overlap when engines shared the chosen variant"}
    return Resolution(reference, protocol)


# --- files -----------------------------------------------------------------


def write_clips(consensus: Consensus, audio: Path, clip_dir: Path) -> list[Path]:
    """
    One 16 kHz mono WAV per listed span, cut from `audio` with ffmpeg
    (`ingest.extract_audio`: re-encoded, so the cut is sample-accurate).
    """
    from talk_autopsy import ingest

    clip_dir.mkdir(parents=True, exist_ok=True)
    return [ingest.extract_audio(audio, clip_dir / clip_name(span), span.start, span.end)
            for span in consensus.listed]


def consensus_to_json(consensus: Consensus) -> str:
    return json.dumps(asdict(consensus), ensure_ascii=False, indent=1) + "\n"


def consensus_from_json(text: str) -> Consensus:
    data = json.loads(text)
    spans = []
    for raw in data.pop("spans"):
        options = [Option(**o) for o in raw.pop("options")]
        spans.append(Span(options=options, **raw))
    return Consensus(spans=spans, **data)
