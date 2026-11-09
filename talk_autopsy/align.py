"""
Forced alignment: a known transcript placed onto its audio, word by word.

    transcript.json (any engine; times may be estimates) + audio.wav
      -> Transcript with the same segments and ids, every word re-timed

Forced alignment does not recognize speech. An acoustic model gives, for every
20 ms frame, the log probability of each character (CTC emissions); a Viterbi
search finds the most probable placement of the given text's characters on
those frames. Word start = the frame its first character is emitted; word end =
the last frame its last character is still the most probable label.

Aligner (chosen 2026-11-09, docs/experiments.md A5 (setup)): the English
character model `WAV2VEC2_ASR_BASE_960H` from torchaudio (wav2vec 2.0 base
fine-tuned on LibriSpeech 960 h, Apache-2.0 weights, about 360 MB, the model
WhisperX uses for English). torchaudio's own `forced_align` is deprecated and
its C++ op is built against one torch version, so the Viterbi search is here, in
numpy. torch and torchaudio are an optional extra (`uv sync --extra align`),
CPU wheels only; the tests never load them.

Long audio is aligned block by block. Blocks of consecutive segments (about a
minute of text) are placed into an audio window that starts at the end of the
previous block's last word and is longer than the block's text could take. The
search has a "garbage" state before and after the text, scored as the best
label of each frame, so the text finds its own place inside the
window instead of being stretched over all of it. The input's own times are
used only for the length of a block, never for its position, so drift in model-
estimated timestamps (Gemini) does not accumulate.

Words the aligner cannot place (no letter or digit, e.g. a lone `-`; or a
block that does not fit its window) get times interpolated between their placed
neighbours, `probability=None`, and are listed in `meta.alignment.unplaced`
as "segment_id:word_index". A placed word's `probability` is the mean emission
probability of its characters at the frames where they are emitted: a low value
means the text and the audio disagree there.
"""

import importlib
import logging
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from talk_autopsy.segments import Segment, Transcript, Word

logger = logging.getLogger(__name__)

BLOCK_CHARS = 900  # about a minute of speech per block
WINDOW_SLACK_SECONDS = 20.0  # extra audio after the block's own (estimated or text-based) length
WINDOW_FACTOR = 1.5  # the block's length is stretched by this before the slack is added
MIN_CHARS_PER_SECOND = 8.0  # a block's length is at least its characters / this
WINDOW_BACKTRACK_SECONDS = 1.0  # a window starts this far before the previous block's last word
# Nats per frame for audio outside the block's text. Must be 0: a positive penalty makes staying
# on the blank inside the text cheaper than garbage, which drags the text to the window's start.
GARBAGE_PENALTY = 0.0
LOW_PROBABILITY = 0.05  # a block whose mean character probability is lower is reported


class Emitter(Protocol):
    """
    The acoustic model: audio samples -> per-frame log probabilities of labels.

    `labels` are the model's output labels; `blank` the index of the CTC blank;
    `word_separator` the label between words (or None); `frame_seconds` the
    frame step. Labels are matched upper-case.
    """

    name: str
    version: str
    labels: tuple[str, ...]
    blank: int
    word_separator: int | None
    frame_seconds: float

    def emissions(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """float32 log probabilities, shape (frames, len(labels)); frame i starts at i * frame_seconds."""
        ...


# --- text -> label ids -------------------------------------------------------

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
         "fifteen sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_SPOKEN_SYMBOLS = {"&": " and ", "@": " at ", "%": " percent ", "+": " plus "}


def number_words(n: int) -> str:
    """0 <= n < 1,000,000 in English words ("2025" -> "two thousand twenty five")."""
    if n < 0 or n >= 1_000_000:
        raise ValueError(f"number out of range: {n}")
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        rest = n % 100
        return _ONES[n // 100] + " hundred" + ("" if rest == 0 else " " + number_words(rest))
    rest = n % 1000
    return number_words(n // 1000) + " thousand" + ("" if rest == 0 else " " + number_words(rest))


def spoken_form(word: str) -> str:
    """
    A written word as letters the acoustic model can match: digits spelled out,
    `&` `@` `%` `+` as words, everything else that is not a letter or an
    apostrophe dropped. `Q&A` -> "Q and A", `Node.js` -> "Nodejs", `4` -> "four".
    The result may be empty (a lone `-`): the word is then unplaceable.
    """
    text = word
    for symbol, spoken in _SPOKEN_SYMBOLS.items():
        text = text.replace(symbol, spoken)
    out, digits = [], ""
    for ch in text + " ":
        if ch.isdigit():
            digits += ch
            continue
        if digits:
            value = int(digits)
            said = number_words(value) if value < 1_000_000 else " ".join(_ONES[int(d)] for d in digits)
            out.append(f" {said} ")
            digits = ""
        if ch.isalpha() or ch == "'":
            out.append(ch)
        elif ch.isspace():
            out.append(" ")
    return " ".join("".join(out).split())


def label_ids(word: str, emitter: Emitter) -> list[int]:
    """Label ids of a word's spoken form; spaces inside it become the word separator (if any)."""
    index = {label.upper(): i for i, label in enumerate(emitter.labels)}
    ids: list[int] = []
    for part in spoken_form(word).upper().split():
        if ids and emitter.word_separator is not None:
            ids.append(emitter.word_separator)
        ids.extend(index[ch] for ch in part if ch in index)
    return ids


# --- Viterbi -------------------------------------------------------------------


@dataclass
class TokenPlacement:
    emit: int  # frame where the token is emitted
    last_voiced: int  # last frame where the token (not the blank) is the better label


def viterbi(emissions: np.ndarray, tokens: list[int], blank: int,
            garbage_penalty: float = GARBAGE_PENALTY) -> tuple[list[TokenPlacement], float] | None:
    """
    Best placement of `tokens`, in order, on the frames of `emissions` (log
    probabilities, frames x labels). Frames before the first and after the last
    token are "garbage", scored as each frame's best label minus
    `garbage_penalty` (0 by default; see GARBAGE_PENALTY). Between two tokens
    the path stays in the earlier one, scored as the better of blank and
    repeating it. Returns one placement per token and the path's total log
    score, or None if the tokens do not fit (fewer frames than tokens).
    """
    frames, count = emissions.shape[0], len(tokens)
    if count == 0:
        raise ValueError("no tokens to align")
    if frames < count:
        return None
    tok = np.asarray(tokens)
    neg = -np.inf
    before, after = 0.0, neg
    score = np.full(count, neg)  # score[j]: best path ending at this frame in token j
    moved = np.zeros((frames, count), dtype=bool)
    after_from_last = np.zeros(frames, dtype=bool)
    for t in range(frames):
        e = emissions[t]
        garbage = float(e.max()) - garbage_penalty
        token_scores = e[tok]
        stay = score + np.maximum(e[blank], token_scores)
        come = np.concatenate(([before], score[:-1])) + token_scores
        moved[t] = come > stay
        new_score = np.where(moved[t], come, stay)
        after_from_last[t] = score[-1] > after
        after = max(after, score[-1]) + garbage
        before = before + garbage
        score = new_score
    total = max(score[-1], after)
    if not math.isfinite(total):
        return None
    placements: list[TokenPlacement | None] = [None] * count
    state = count if after > score[-1] else count - 1  # count = the "after" garbage state
    last_voiced: dict[int, int] = {}
    for t in range(frames - 1, -1, -1):
        if state == count:
            if after_from_last[t]:
                state = count - 1
            continue
        e = emissions[t]
        if e[tok[state]] >= e[blank]:
            last_voiced.setdefault(state, t)
        if moved[t, state]:
            placements[state] = TokenPlacement(emit=t, last_voiced=last_voiced.get(state, t))
            state -= 1
            if state < 0:
                break
    if any(p is None for p in placements):
        raise AssertionError("Viterbi backtrack did not place every token")
    return placements, float(total)  # type: ignore[return-value]


# --- blocks of words -------------------------------------------------------------


@dataclass
class _Word:
    segment_index: int
    word_index: int
    text: str
    ids: list[int]
    start: float | None = None
    end: float | None = None
    probability: float | None = None


@dataclass
class _Block:
    words: list[_Word] = field(default_factory=list)
    estimated_seconds: float = 0.0
    chars: int = 0


def _blocks(transcript: Transcript, emitter: Emitter) -> tuple[list[_Block], list[_Word]]:
    blocks, everything, current = [], [], _Block()
    for s_index, segment in enumerate(transcript.segments):
        words = [_Word(s_index, w_index, chunk, label_ids(chunk, emitter))
                 for w_index, chunk in enumerate(segment.text.split())]
        everything.extend(words)
        current.words.extend(words)
        current.estimated_seconds += max(0.0, segment.end - segment.start)
        current.chars += len(segment.text)
        if current.chars >= BLOCK_CHARS:
            blocks.append(current)
            current = _Block()
    if current.words:
        blocks.append(current)
    return blocks, everything


def _block_tokens(block: _Block, emitter: Emitter) -> tuple[list[int], list[tuple[_Word, int, int]]]:
    """All placeable words' ids joined by the word separator; per word its token range."""
    tokens, ranges = [], []
    for word in block.words:
        if not word.ids:
            continue
        if tokens and emitter.word_separator is not None:
            tokens.append(emitter.word_separator)
        ranges.append((word, len(tokens), len(tokens) + len(word.ids)))
        tokens.extend(word.ids)
    return tokens, ranges


def _place_block(block: _Block, emissions: np.ndarray, emitter: Emitter, cursor_frame: int) -> int | None:
    """Place the block's words into the window after `cursor_frame`; the new cursor, or None if it failed."""
    tokens, ranges = _block_tokens(block, emitter)
    if not tokens:
        return cursor_frame
    fs = emitter.frame_seconds
    length = max(block.estimated_seconds, block.chars / MIN_CHARS_PER_SECOND) * WINDOW_FACTOR
    first = max(0, cursor_frame - round(WINDOW_BACKTRACK_SECONDS / fs))
    last = emissions.shape[0]
    for window_end in (min(last, first + round((length + WINDOW_SLACK_SECONDS) / fs)), last):
        result = viterbi(emissions[first:window_end], tokens, emitter.blank)
        if result is not None:
            break
    else:
        return None
    placements, _ = result
    for word, a, b in ranges:
        emit = [first + placements[k].emit for k in range(a, b)]
        word.start = round(emit[0] * fs, 3)
        word.end = round((first + placements[b - 1].last_voiced + 1) * fs, 3)
        probs = [math.exp(float(emissions[f, tokens[k]])) for f, k in zip(emit, range(a, b), strict=True)]
        word.probability = round(sum(probs) / len(probs), 4)
    return first + placements[-1].last_voiced + 1


def _interpolate(words: list[_Word], audio_seconds: float) -> list[_Word]:
    """Give unplaced words times between their placed neighbours, shared by character count."""
    unplaced = []
    i = 0
    while i < len(words):
        if words[i].start is not None:
            i += 1
            continue
        j = i
        while j < len(words) and words[j].start is None:
            j += 1
        left = words[i - 1].end if i > 0 else 0.0
        right = words[j].start if j < len(words) else audio_seconds
        right = max(left, right)
        weights = [max(1, len(w.text)) for w in words[i:j]]
        total, t = sum(weights), left
        for w, weight in zip(words[i:j], weights, strict=True):
            w.start = round(t, 3)
            t += (right - left) * weight / total
            w.end = round(t, 3)
            unplaced.append(w)
        i = j
    return unplaced


# --- the stage -------------------------------------------------------------------


def align(transcript: Transcript, audio_path: Path, emitter: Emitter | None = None) -> Transcript:
    """
    `transcript` with the same segments, ids and texts, every word re-timed on
    `audio_path` (16 kHz mono 16-bit WAV); segment start and end from their first
    and last word; `meta.timestamps = "aligned"` and `meta.alignment` with the
    aligner, timings and the unplaced words.
    """
    from talk_autopsy.stt.faster_whisper import read_wav  # reads and checks the WAV format

    audio_path = Path(audio_path)
    if not audio_path.is_file():
        raise FileNotFoundError(f"No such file: {audio_path}")
    if not transcript.segments:
        raise ValueError("the transcript has no segments")
    samples = read_wav(audio_path)
    sample_rate = 16000
    audio_seconds = len(samples) / sample_rate
    emitter = emitter or Wav2Vec2Emitter()

    started = time.perf_counter()
    emissions = emitter.emissions(samples, sample_rate)
    emission_seconds = time.perf_counter() - started
    fs = emitter.frame_seconds
    blocks, words = _blocks(transcript, emitter)
    cursor, failed_blocks, low_blocks = 0, [], []
    for number, block in enumerate(blocks, start=1):
        new_cursor = _place_block(block, emissions, emitter, cursor)
        if new_cursor is None:
            failed_blocks.append(number)
            logger.warning("Alignment: block %d of %d does not fit the rest of the audio", number,
                           len(blocks))
            continue
        probs = [w.probability for w in block.words if w.probability is not None]
        if probs and sum(probs) / len(probs) < LOW_PROBABILITY:
            low_blocks.append(number)
        cursor = new_cursor
    unplaced = _interpolate(words, audio_seconds)
    wall = time.perf_counter() - started

    by_segment: dict[int, list[_Word]] = {}
    for w in words:
        by_segment.setdefault(w.segment_index, []).append(w)
    segments = []
    for s_index, segment in enumerate(transcript.segments):
        timed = by_segment.get(s_index, [])
        if not timed:  # a segment with no text keeps its times; nothing to place
            segments.append(segment.model_copy())
            continue
        new_words = [Word(start=w.start, end=max(w.start, w.end), text=w.text, probability=w.probability)
                     for w in timed]
        segments.append(Segment(id=segment.id, start=new_words[0].start,
                                end=max(new_words[-1].end, new_words[0].start),
                                text=segment.text, words=new_words, confidence=segment.confidence))
    total_words = len(words)
    logger.info("Aligned %d words in %d blocks: %.1f s (%.1f s acoustic model); %d unplaced", total_words,
                len(blocks), wall, emission_seconds, len(unplaced))
    meta = dict(transcript.meta)
    meta["timestamps_before_alignment"] = transcript.meta.get("timestamps")
    meta["timestamps"] = "aligned"
    meta["alignment"] = {
        "aligner": emitter.name,
        "aligner_version": emitter.version,
        "input": audio_path.name,
        "audio_seconds": round(audio_seconds, 3),
        "frame_seconds": fs,
        "blocks": len(blocks),
        "blocks_failed": failed_blocks,
        "blocks_low_probability": low_blocks,
        "words": total_words,
        "unplaced_words": len(unplaced),
        "unplaced_share": round(len(unplaced) / total_words, 4) if total_words else None,
        "unplaced": [f"{transcript.segments[w.segment_index].id}:{w.word_index}" for w in unplaced],
        "acoustic_model_seconds": round(emission_seconds, 1),
        "wall_seconds": round(wall, 1),
        "realtime_factor": round(audio_seconds / wall, 2) if wall > 0 else None,
    }
    return Transcript(segments=segments, language=transcript.language,
                      source=f"{transcript.source} + aligned", meta=meta)


# --- the real acoustic model -------------------------------------------------------


class Wav2Vec2Emitter:
    """
    torchaudio's WAV2VEC2_ASR_BASE_960H on the CPU. The audio goes through the
    model in pieces of CHUNK_SECONDS (attention over a whole talk would not fit in
    memory); each piece yields exactly its length / frame step frames.
    """

    bundle_name = "WAV2VEC2_ASR_BASE_960H"
    CHUNK_SECONDS = 30
    frame_seconds = 0.02  # the model's 320-sample stride at 16 kHz

    def __init__(self, threads: int | None = None):
        try:
            self._torch = importlib.import_module("torch")
            self._torchaudio = importlib.import_module("torchaudio")
        except ImportError:
            raise RuntimeError("forced alignment needs torch and torchaudio: uv sync --extra align") from None
        self._torch.set_num_threads(threads or os.cpu_count() or 4)
        self._bundle = getattr(self._torchaudio.pipelines, self.bundle_name)
        self.labels = tuple(self._bundle.get_labels())
        self.blank = self.labels.index("-")
        self.word_separator = self.labels.index("|")
        self.name = f"torchaudio {self.bundle_name}"
        self.version = f"torch {self._torch.__version__}, torchaudio {self._torchaudio.__version__}"
        self._model = None

    def emissions(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        torch = self._torch
        if sample_rate != self._bundle.sample_rate:
            raise ValueError(f"the aligner needs {self._bundle.sample_rate} Hz audio, got {sample_rate}")
        if self._model is None:
            started = time.perf_counter()
            self._model = self._bundle.get_model().eval()
            logger.info("Loaded %s in %.1f s", self.bundle_name, time.perf_counter() - started)
        step = round(self.frame_seconds * sample_rate)
        chunk = self.CHUNK_SECONDS * sample_rate
        pieces = []
        with torch.inference_mode():
            for begin in range(0, len(samples), chunk):
                piece = samples[begin:begin + chunk]
                frames = math.ceil(len(piece) / step)
                if len(piece) < 400:  # shorter than the model's receptive field
                    piece = np.pad(piece, (0, 400 - len(piece)))
                logits, _ = self._model(torch.from_numpy(np.ascontiguousarray(piece)).unsqueeze(0))
                out = torch.log_softmax(logits[0], dim=-1).numpy()
                if out.shape[0] < frames:
                    out = np.concatenate([out, np.repeat(out[-1:], frames - out.shape[0], axis=0)])
                pieces.append(out[:frames])
        return np.concatenate(pieces).astype(np.float32)


# --- agreement with another transcript's word times -------------------------------------


def start_differences(aligned: Transcript, other: Transcript) -> list[float]:
    """
    Absolute start-time differences (seconds) between the words of two timed
    transcripts on the words they agree on: both are tokenized like the metrics
    (adjudicate.backbone_tokens) and matched with difflib; only equal runs count.
    Used to compare forced alignment with faster-whisper's own word times, a
    proxy for a hand-labelled reference, not a reference.
    """
    import difflib

    from talk_autopsy.adjudicate import backbone_tokens

    a, b = backbone_tokens(aligned), backbone_tokens(other)
    matcher = difflib.SequenceMatcher(None, [t.norm for t in a], [t.norm for t in b], autojunk=False)
    diffs = []
    for tag, i1, i2, j1, _ in matcher.get_opcodes():
        if tag == "equal":
            diffs.extend(abs(a[i1 + k].start - b[j1 + k].start) for k in range(i2 - i1))
    return diffs
