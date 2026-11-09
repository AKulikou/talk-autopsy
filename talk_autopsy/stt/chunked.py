"""
Long audio through Gemini: chunks cut at silences, a per-chunk cache, model
fallback, faster-whisper as the last resort, progress events.

    audio.wav -> chunks of about 10 min (cut at the longest silence near each boundary)
      -> per chunk: Gemini (model list, retries) | faster-whisper for that chunk only
      -> times shifted by the chunk offset -> one Transcript, segments numbered once

- **Chunks.** Audio up to `chunk_seconds` (600) is one chunk. Longer audio is
  cut near every `chunk_seconds` after the previous cut, at the middle of the
  longest silence (ffmpeg `silencedetect`) whose middle lies within
  `search_seconds` (30) of that point; with no silence there, at the point
  itself. No overlap: every second of audio is in exactly one chunk.
- **Cache** (requirement F9): each finished chunk is written to
  `cache_dir/chunk-<i>-<key>.json`; a rerun loads it instead of calling any
  engine. The key hashes the audio content, the chunk's start and end, the
  model list, the Gemini prompt and the glossary content when it is sent.
- **Resilience.** Each model of the list is tried in order (the per-model engine
  retries 503 and per-minute 429 with waits). A model that stays unavailable
  or answers with something unusable (cut off, malformed) passes the chunk to
  the next model; after the last, faster-whisper transcribes that chunk only.
  The engine and model of every chunk are in `meta.chunks`. A daily-quota or
  billing error (`GeminiQuotaExhausted`) stops the run with a clear error: no
  other Gemini model would work today, and falling through to faster-whisper
  for every remaining chunk would turn a 15-minute run into hours without
  anyone deciding it. Finished chunks stay cached for the rerun.
- **Progress events** (requirement F13, for the future web UI): every step
  calls `progress(ProgressEvent)`; the CLI prints one line per event.

Times inside a Gemini chunk are model estimates (`meta.timestamps =
"model-estimated"`); they are clamped to their chunk so chunks stay in order.
Forced alignment (align.py) replaces all of them with measured times.
"""

import hashlib
import json
import logging
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from talk_autopsy import config, ingest
from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Segment, Transcript, Word, load_transcript, save_transcript
from talk_autopsy.stt import gemini

logger = logging.getLogger(__name__)

CHUNK_SECONDS = 600.0
SEARCH_SECONDS = 30.0
SILENCE_NOISE_DB = -30
SILENCE_MIN_SECONDS = 0.3


@dataclass(frozen=True)
class ProgressEvent:
    stage: str  # e.g. "stt"
    event: str  # "plan", "chunk-start", "chunk-cached", "chunk-done", "fallback", "done"
    chunk: int  # 1-based; 0 for whole-run events
    chunks: int
    audio_seconds_done: float
    audio_seconds_total: float
    elapsed: float
    engine: str
    detail: str = ""

    def line(self) -> str:
        """One human-readable line for the CLI."""
        part = f"chunk {self.chunk}/{self.chunks}" if self.chunk else f"{self.chunks} chunk(s)"
        detail = f" - {self.detail}" if self.detail else ""
        return (f"[{self.stage}] {self.event:<12} {part:<12} {self.audio_seconds_done:7.0f}/"
                f"{self.audio_seconds_total:.0f} s audio  {self.elapsed:7.1f} s  {self.engine}{detail}")


ProgressCallback = Callable[[ProgressEvent], None]


# --- chunk plan ---------------------------------------------------------------


_SILENCE = re.compile(r"silence_(start|end): (-?[\d.]+)")


def parse_silences(ffmpeg_output: str, duration: float) -> list[tuple[float, float]]:
    """(start, end) pairs from ffmpeg silencedetect output; a silence open at the end ends at `duration`."""
    silences, start = [], None
    for kind, value in _SILENCE.findall(ffmpeg_output):
        if kind == "start":
            start = max(0.0, float(value))
        elif start is not None:
            silences.append((start, float(value)))
            start = None
    if start is not None:
        silences.append((start, duration))
    return silences


def detect_silences(audio: Path, duration: float) -> list[tuple[float, float]]:
    """Silences of at least SILENCE_MIN_SECONDS below SILENCE_NOISE_DB (ffmpeg silencedetect)."""
    command = [ingest._tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(audio), "-af",
               f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_SECONDS}", "-f", "null", "-"]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg silencedetect failed on {audio}: {result.stderr.strip()[-500:]}")
    return parse_silences(result.stderr, duration)


def plan_chunks(duration: float, silences: list[tuple[float, float]], chunk_seconds: float = CHUNK_SECONDS,
                search_seconds: float = SEARCH_SECONDS) -> list[tuple[float, float]]:
    """
    Chunk (start, end) pairs covering [0, duration] without overlap. A cut goes
    near every `chunk_seconds` after the previous cut: at the middle of the
    longest silence whose middle is within `search_seconds` of that point, else
    at the point itself.
    """
    if duration <= 0:
        raise ValueError(f"audio duration must be positive, got {duration}")
    cuts, previous = [], 0.0
    while duration - previous > chunk_seconds:
        target = previous + chunk_seconds
        middles = [(end - start, (start + end) / 2) for start, end in silences]
        near = [(length, middle) for length, middle in middles
                if abs(middle - target) <= search_seconds and previous < middle < duration]
        cut = max(near)[1] if near else target
        cuts.append(round(cut, 3))
        previous = cut
    bounds = [0.0, *cuts, round(duration, 3)]
    return list(zip(bounds[:-1], bounds[1:], strict=True))


# --- cache --------------------------------------------------------------------


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_key(audio_hash: str, start: float, end: float, models: list[str], prompt: str) -> str:
    """The prompt includes the glossary when it is sent, so the glossary content is part of the key."""
    payload = json.dumps([audio_hash, round(start, 3), round(end, 3), models, prompt], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# --- the engine ---------------------------------------------------------------


def _shift(segments: list[Segment], offset: float, limit: float) -> tuple[list[Segment], int]:
    """Segments moved by `offset`, clamped to [offset, limit]; returns them and how many were clamped."""
    shifted, clamped = [], 0
    for s in segments:
        start, end = s.start + offset, s.end + offset
        if end > limit or start > limit:
            clamped += 1
        start, end = min(start, limit), min(end, limit)
        words = None
        if s.words is not None:
            words = [Word(start=min(w.start + offset, limit), end=min(w.end + offset, limit), text=w.text,
                          probability=w.probability) for w in s.words]
        shifted.append(Segment(id=s.id, start=start, end=end, text=s.text, words=words,
                               confidence=s.confidence))
    return shifted, clamped


class ChunkedGeminiTranscriber:
    """
    Gemini over long audio. `gemini_factory(model)` and `whisper_factory()` build
    the per-chunk engines (defaults: GeminiTranscriber without its own quota
    fallback, and faster-whisper large-v3 on all CPU threads); tests pass fakes.
    """

    language = "en"

    def __init__(self, models: list[str] | None = None, glossary_prompt: bool = False,
                 cache_dir: Path | None = None, progress: ProgressCallback | None = None,
                 gemini_factory: Callable[[str], object] | None = None,
                 whisper_factory: Callable[[], object] | None = None,
                 chunk_seconds: float = CHUNK_SECONDS, search_seconds: float = SEARCH_SECONDS):
        self.models = list(models or config.GEMINI_MODELS)
        if not self.models:
            raise ValueError("the Gemini model list is empty")
        self.glossary_prompt = glossary_prompt
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.progress = progress or (lambda event: None)
        self.gemini_factory = gemini_factory or (lambda model: gemini.GeminiTranscriber(
            model=model, glossary_prompt=glossary_prompt, quota_fallback=False))
        self.whisper_factory = whisper_factory or _default_whisper
        self.chunk_seconds = chunk_seconds
        self.search_seconds = search_seconds
        self.name = f"gemini chunked {' > '.join(self.models)}"

    def _chunk(self, wav: Path, glossary: Glossary | None, emit) -> tuple[Transcript, dict]:
        """Transcribe one chunk file: each model in turn, then faster-whisper. Returns it and its record."""
        errors: list[dict] = []
        for model in self.models:
            engine = self.gemini_factory(model)
            try:
                result = engine.transcribe(wav, glossary)
            except gemini.GeminiQuotaExhausted:
                raise
            except (gemini.GeminiUnavailable, ValueError, RuntimeError) as e:
                errors.append({"model": model, "error": str(e),
                               "failed_attempts": getattr(e, "failed_attempts", [])})
                emit("fallback", f"{model}: {e}")
                continue
            return result, {"engine": "gemini", "model": result.meta.get("model", model), "errors": errors,
                            "failed_attempts": result.meta.get("failed_attempts", [])}
        emit("fallback", "every Gemini model failed; faster-whisper for this chunk")
        result = self.whisper_factory().transcribe(wav, glossary)
        return result, {"engine": "faster-whisper", "model": result.meta.get("model"), "errors": errors,
                        "failed_attempts": []}

    def transcribe(self, audio_path: Path, glossary: Glossary | None) -> Transcript:
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise FileNotFoundError(f"No such file: {audio_path}")
        if self.glossary_prompt and glossary is None:
            raise ValueError("glossary_prompt is on but no glossary was given")
        started = time.perf_counter()
        duration = gemini.wav_seconds(audio_path)
        silences = detect_silences(audio_path, duration) if duration > self.chunk_seconds else []
        plan = plan_chunks(duration, silences, self.chunk_seconds, self.search_seconds)
        prompt = gemini.build_prompt(glossary if self.glossary_prompt else None)
        audio_hash = file_sha256(audio_path)
        state = {"done": 0.0, "engine": "-"}

        def emit(event: str, detail: str = "", chunk: int = 0) -> None:
            self.progress(ProgressEvent("stt", event, chunk, len(plan), state["done"], duration,
                                        round(time.perf_counter() - started, 1), state["engine"], detail))

        emit("plan", f"cuts at {', '.join(f'{end:.1f}' for _, end in plan[:-1]) or 'none'}")
        segments: list[Segment] = []
        records: list[dict] = []
        tokens = {"prompt": 0, "output": 0, "thinking": 0}
        cost = paid = 0.0
        clamped_total = 0
        with tempfile.TemporaryDirectory(prefix="talk-autopsy-chunks-") as tmp:
            for number, (start, end) in enumerate(plan, start=1):
                key = cache_key(audio_hash, start, end, self.models, prompt)
                cached_path = self.cache_dir / f"chunk-{number:03d}-{key}.json" if self.cache_dir else None
                chunk_started = time.perf_counter()
                if cached_path is not None and cached_path.is_file():
                    chunk = load_transcript(cached_path)
                    record = dict(chunk.meta.get("chunk_record", {}), cached=True)
                    state["engine"] = record.get("model") or record.get("engine", "?")
                    emit("chunk-cached", cached_path.name, number)
                else:
                    state["engine"] = self.models[0]
                    emit("chunk-start", f"{start:.1f}-{end:.1f} s", number)
                    if len(plan) == 1:
                        wav = audio_path
                    else:
                        wav = Path(tmp) / f"chunk-{number:03d}.wav"
                        ingest.extract_audio(audio_path, wav, start, end)
                    try:
                        chunk, record = self._chunk(wav, glossary, lambda e, d, n=number: emit(e, d, n))
                    except gemini.GeminiQuotaExhausted as e:
                        where = f"; finished chunks are cached in {self.cache_dir}" if self.cache_dir else ""
                        raise gemini.GeminiQuotaExhausted(
                            f"{e}. Stopped at chunk {number} of {len(plan)}{where}. Gemini will not work "
                            "with this key until the quota resets; rerun then, or choose another engine.",
                            e.model) from e
                    record["cached"] = False
                    chunk.meta["chunk_record"] = record
                    if cached_path is not None:
                        save_transcript(chunk, cached_path)
                    state["engine"] = record.get("model") or record["engine"]
                shifted, clamped = _shift(chunk.segments, start, end)
                clamped_total += clamped
                segments.extend(shifted)
                meta = chunk.meta
                for name in tokens:
                    tokens[name] += (meta.get("tokens") or {}).get(name, 0) or 0
                cost += meta.get("cost_usd") or 0.0
                paid += meta.get("cost_usd_at_paid_prices") or 0.0
                records.append({"index": number, "start": start, "end": end, **record,
                                "segments": len(chunk.segments),
                                "wall_seconds": meta.get("wall_seconds"),
                                "run_seconds_now": round(time.perf_counter() - chunk_started, 1)})
                state["done"] = end
                emit("chunk-done", f"{len(chunk.segments)} segments", number)
        wall = time.perf_counter() - started
        renumbered = [s.model_copy(update={"id": n}) for n, s in enumerate(segments, start=1)]
        emit("done", f"{len(renumbered)} segments")
        engines = sorted({r["engine"] for r in records})
        return Transcript(
            segments=renumbered,
            language=self.language,
            source=self.name,
            meta={
                "engine": "gemini-chunked",
                "models": self.models,
                "glossary_prompt": self.glossary_prompt,
                "prompt": prompt,
                "input": audio_path.name,
                "audio_seconds": round(duration, 3),
                "audio_sha256": audio_hash,
                "chunk_seconds": self.chunk_seconds,
                "chunks": records,
                "chunk_engines": engines,
                "timestamps": "model-estimated" if "gemini" in engines else "measured",
                "segments_clamped_to_chunk": clamped_total,
                "wall_seconds": round(wall, 1),
                "realtime_factor": round(duration / wall, 3) if wall > 0 else None,
                "tokens": tokens,
                "cost_usd": round(cost, 6),
                "cost_usd_at_paid_prices": round(paid, 6),
            },
        )


def _default_whisper():
    import os

    from talk_autopsy.stt.faster_whisper import FasterWhisperTranscriber

    return FasterWhisperTranscriber(cpu_threads=os.cpu_count() or 0)


def record_json(event: ProgressEvent) -> str:
    """The event as one JSON line (for a log or a web UI)."""
    return json.dumps(asdict(event), ensure_ascii=False)
