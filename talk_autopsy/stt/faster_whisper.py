"""
The first engine: faster-whisper (CTranslate2 Whisper) running locally on the CPU.

    audio.wav -> raw.json (Transcript with word timings and probabilities)

Settings are fixed for the benchmark: large-v3, int8 weights on the CPU, beam
size 5, Silero VAD on, word timestamps, English. The options are a glossary
prompt (Whisper's `initial_prompt`), off by default: it is hypothesis A2 and is
measured against the run without it; and the number of CPU threads
(`cpu_threads`; 0 = faster-whisper's default of 4), which changes speed but not
the decoded text.

Segments whose text has no word (only `.`, typical at the end of the audio) are
dropped before the transcript is written, and ids are assigned after dropping
(reference conventions in data/test-set/README.md).

Segment `confidence` is exp(avg_logprob). faster-whisper computes avg_logprob
once per 30-second decoding window, so every segment decoded in the same window
gets the same confidence; the per-word `probability` is the finer signal.

The audio is read here (`read_wav`) and handed to faster-whisper as samples, not
as a path: faster-whisper 1.2.1 decodes files with PyAV and passes an argument
(`metadata_errors`) that PyAV 19 no longer accepts. The input is always the
16 kHz mono 16-bit WAV that `ingest.extract_audio` writes, so no decoder is
needed.

The `faster_whisper` package is imported when the model loads, not at import of
this module: the model is large and slow to load, and tests replace the package
with a fake.
"""

import importlib
import logging
import math
import os
import time
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

from talk_autopsy.glossary import Glossary
from talk_autopsy.ingest import SAMPLE_RATE
from talk_autopsy.segments import Segment, Transcript, Word
from talk_autopsy.stt.base import drop_wordless

logger = logging.getLogger(__name__)

# Whisper's decoder takes at most 224 prompt tokens (half its 448-token context);
# the glossary prompt stays well below so the prompt is never cut mid-term.
PROMPT_TOKEN_LIMIT = 224
DEFAULT_PROMPT_TOKENS = 200

PROGRESS_EVERY_SECONDS = 60.0  # log progress once per minute of audio
DEFAULT_CPU_THREADS = 4  # what faster-whisper uses when cpu_threads is 0


def estimate_tokens(text: str) -> int:
    """
    A deliberately high token estimate, used only when no Whisper tokenizer is at
    hand: one token per two characters. Real BPE averages about four characters
    per English token and fewer for identifiers, so this errs towards a shorter
    prompt.
    """
    return math.ceil(len(text) / 2)


def build_glossary_prompt(glossary: Glossary, max_tokens: int = DEFAULT_PROMPT_TOKENS,
                          count_tokens: Callable[[str], int] = estimate_tokens) -> str | None:
    """
    Whisper `initial_prompt` from the glossary: the source terms in glossary order,
    joined with ", ". Terms are added whole while the prompt stays within
    `max_tokens` (counted by `count_tokens`); a term that would exceed it and all
    after it are left out. None if the glossary has no terms or the first does
    not fit.
    """
    if max_tokens > PROMPT_TOKEN_LIMIT:
        raise ValueError(f"max_tokens {max_tokens} is above Whisper's prompt limit of {PROMPT_TOKEN_LIMIT}")
    terms: list[str] = []
    for entry in glossary.entries:
        candidate = ", ".join([*terms, entry.term])
        if count_tokens(candidate) > max_tokens:
            logger.info("Glossary prompt: %d of %d terms fit in %d tokens",
                        len(terms), len(glossary.entries), max_tokens)
            break
        terms.append(entry.term)
    return ", ".join(terms) if terms else None


def _model_repo(model_size: str) -> str:
    """Hugging Face repository faster-whisper downloads a model size from."""
    from faster_whisper import utils  # the real package: only called outside tests

    return utils._MODELS.get(model_size, model_size)


def announce_download(model_size: str) -> None:
    """
    If the model is not in the Hugging Face cache yet, log where it will go and
    how big it is before faster-whisper starts the download.
    """
    from huggingface_hub import HfApi, constants, try_to_load_from_cache

    repo = _model_repo(model_size)
    if Path(model_size).is_dir() or isinstance(try_to_load_from_cache(repo, "model.bin"), str):
        return
    try:
        info = HfApi().model_info(repo, files_metadata=True)
        size = f"{sum(f.size or 0 for f in info.siblings) / 1e9:.2f} GB"
    except Exception as e:  # the size is information only; the download reports its own errors
        size = f"size unknown ({type(e).__name__})"
    logger.info("Model %s is not cached: downloading %s (%s) to %s", model_size, repo, size,
                constants.HF_HUB_CACHE)


def read_wav(path: Path) -> np.ndarray:
    """
    Samples of a 16 kHz mono 16-bit PCM WAV as float32 in [-1, 1), the input
    faster-whisper expects. Any other format is an error that names the `cut`
    command: resampling here would hide which audio was measured.
    """
    try:
        with wave.open(str(path), "rb") as w:
            channels, rate, width = w.getnchannels(), w.getframerate(), w.getsampwidth()
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as e:
        raise ValueError(f"{path} is not a PCM WAV file ({e}); make one with talk-autopsy cut") from None
    if (channels, rate, width) != (1, SAMPLE_RATE, 2):
        raise ValueError(
            f"{path}: {channels} channel(s), {rate} Hz, {width * 8}-bit; expected mono {SAMPLE_RATE} Hz "
            "16-bit, as written by talk-autopsy cut"
        )
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0


def _mmss(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


class FasterWhisperTranscriber:
    """Transcriber backed by faster-whisper; large-v3 on CPU with int8 weights by default."""

    language = "en"
    beam_size = 5
    vad_filter = True

    def __init__(self, model_size: str = "large-v3", device: str = "cpu", compute_type: str = "int8",
                 glossary_prompt: bool = False, prompt_tokens: int = DEFAULT_PROMPT_TOKENS,
                 cpu_threads: int = 0):
        if cpu_threads < 0:
            raise ValueError(f"cpu_threads must be 0 (default) or positive, got {cpu_threads}")
        self.model_size = model_size
        self.cpu_threads = cpu_threads
        self.device = device
        self.compute_type = compute_type
        self.glossary_prompt = glossary_prompt
        self.prompt_tokens = prompt_tokens
        self.name = f"faster-whisper {model_size}"

    def _load(self):
        faster_whisper = importlib.import_module("faster_whisper")
        announce_download(self.model_size)
        started = time.perf_counter()
        model = faster_whisper.WhisperModel(self.model_size, device=self.device,
                                            compute_type=self.compute_type, cpu_threads=self.cpu_threads)
        seconds = time.perf_counter() - started
        logger.info("Loaded %s (%s, %s, %d CPU threads) in %.1f s", self.model_size, self.device,
                    self.compute_type, self.cpu_threads or DEFAULT_CPU_THREADS, seconds)
        return model, seconds, getattr(faster_whisper, "__version__", "unknown")

    @staticmethod
    def _token_counter(model) -> Callable[[str], int]:
        tokenizer = getattr(model, "hf_tokenizer", None)
        if tokenizer is None:
            return estimate_tokens
        # faster-whisper encodes the prompt as " " + prompt.strip()
        return lambda text: len(tokenizer.encode(" " + text.strip(), add_special_tokens=False).ids)

    def transcribe(self, audio_path: Path, glossary: Glossary | None) -> Transcript:
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise FileNotFoundError(f"No such file: {audio_path}")
        if self.glossary_prompt and glossary is None:
            raise ValueError("glossary_prompt is on but no glossary was given")
        samples = read_wav(audio_path)  # before the model loads: a wrong file fails in a second

        model, load_seconds, version = self._load()
        prompt = None
        if self.glossary_prompt:
            prompt = build_glossary_prompt(glossary, self.prompt_tokens, self._token_counter(model))
            if prompt is None:
                raise ValueError("glossary_prompt is on but no glossary term fits the prompt")
        logger.info("Initial prompt: %s", repr(prompt) if prompt else "none")

        started = time.perf_counter()
        pieces, info = model.transcribe(
            samples,
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=self.vad_filter,
            word_timestamps=True,
            initial_prompt=prompt,
        )
        segments: list[Segment] = []
        next_report = PROGRESS_EVERY_SECONDS
        for number, piece in enumerate(pieces, start=1):  # the generator does the decoding
            words = [Word(start=w.start, end=w.end, text=w.word.strip(), probability=w.probability)
                     for w in (piece.words or [])]
            segments.append(Segment(id=number, start=piece.start, end=piece.end, text=piece.text.strip(),
                                    words=words, confidence=math.exp(piece.avg_logprob)))
            if piece.end >= next_report:
                logger.info("  %s / %s of audio, %d segments, %.0f s elapsed", _mmss(piece.end),
                            _mmss(info.duration), number, time.perf_counter() - started)
                next_report = (piece.end // PROGRESS_EVERY_SECONDS + 1) * PROGRESS_EVERY_SECONDS
        transcribe_seconds = time.perf_counter() - started
        segments, dropped = drop_wordless(segments)  # e.g. trailing "." segments; ids renumbered
        if dropped:
            logger.info("Dropped %d segment(s) with no word", dropped)
        realtime = info.duration / transcribe_seconds if transcribe_seconds > 0 else None
        logger.info("Transcribed %.1f s of audio in %.1f s (realtime factor %s; model load %.1f s)",
                    info.duration, transcribe_seconds, f"{realtime:.2f}" if realtime else "n/a", load_seconds)

        return Transcript(
            segments=segments,
            language=self.language,
            source=self.name,
            meta={
                "engine": "faster-whisper",
                "engine_version": version,
                "model": self.model_size,
                "device": self.device,
                "compute_type": self.compute_type,
                "beam_size": self.beam_size,
                "vad_filter": self.vad_filter,
                "glossary_prompt": self.glossary_prompt,
                "initial_prompt": prompt,
                "input": audio_path.name,
                "segments_dropped_wordless": dropped,
                "audio_seconds": round(info.duration, 3),
                "audio_seconds_after_vad": round(getattr(info, "duration_after_vad", info.duration), 3),
                "load_seconds": round(load_seconds, 1),
                "transcribe_seconds": round(transcribe_seconds, 1),
                "wall_seconds": round(load_seconds + transcribe_seconds, 1),
                # audio seconds per wall second of decoding, model load excluded; > 1 = faster than real time
                "realtime_factor": round(realtime, 3) if realtime else None,
                "cpu_count": os.cpu_count(),
                "cpu_threads": self.cpu_threads or DEFAULT_CPU_THREADS,
                "cost_usd": 0.0,  # runs locally: no API cost
            },
        )
