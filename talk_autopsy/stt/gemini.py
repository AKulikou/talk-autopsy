"""
Cloud engine: Gemini, audio in, verbatim transcript as JSON out.

    audio.wav -> Files API upload -> generate_content (structured JSON) -> raw.json

Checked on 2026-11-09 against the Gemini API docs (models, audio understanding,
Files API, structured output, pricing, rate limits; ai.google.dev now redirects
to aistudio.google.com/docs) and the installed SDK `google-genai` 3.0.0:

- Models: the flagship `gemini-4-argon` is not on the free tier. The default is
  `gemini-3.8-flash` ("our most intelligent Flash model", free tier); on a
  quota rejection (HTTP 429 RESOURCE_EXHAUSTED, `errors.ClientError`) the
  engine retries once with `gemini-3.6-flash`. A server error (HTTP 5xx, such
  as 503 "high demand") is retried on the same model after 20, 60 and 120 s;
  failed attempts are listed in `meta.failed_attempts`. `meta.model` is the model that
  actually ran. Both are set in config.py and overridable through .env.
- Audio: 32 tokens per second; a 10-minute 16 kHz mono WAV is about 19 MB, so
  it goes through the Files API (inline data is limited to 20 MB per request),
  is waited on until ACTIVE and deleted after the call (the service would
  delete it after 48 hours anyway).
- Structured output: `response_mime_type="application/json"` with
  `response_json_schema`. The text is parsed strictly: malformed JSON, a wrong
  shape, an end before a start or a cut-off answer is an error, never repaired.
- Prices per million tokens, paid Standard tier, audio not priced separately:
  gemini-3.8-flash and gemini-3.6-flash $0.75 input / $3.75 output including
  thinking (through 2026-12-31); gemini-4-argon $2.00 / $10.00 (through
  2026-11-30). On the free tier (`GEMINI_TIER=free`, the default) `cost_usd`
  is 0 and `cost_usd_at_paid_prices` keeps the estimate; tokens are always
  recorded.

Gemini's timestamps are the model's estimates, not measurements:
`meta.timestamps = "model-estimated"`, every segment has `confidence=None` and
no words. Nothing downstream may use these times for subtitles; the consensus
alignment uses only the text.

The client is created with the explicit GEMINI_API_KEY. If GOOGLE_API_KEY is
also set in the environment the SDK logs that it uses GOOGLE_API_KEY, but an
explicit key takes precedence (`api_key or env_api_key` in the SDK client).

The SDK is imported when a run starts, so tests can replace it with a fake.
"""

import importlib
import json
import logging
import time
import wave
from pathlib import Path

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from talk_autopsy import config
from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Segment, Transcript
from talk_autopsy.stt.base import drop_wordless

logger = logging.getLogger(__name__)

# USD per million tokens (input, output incl. thinking), paid Standard tier, checked 2026-11-09.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-4-argon": (2.00, 10.00),
}
PRICES_CHECKED = "2026-11-09"

MAX_OUTPUT_TOKENS = 32768  # a 10-minute transcript as JSON is a few thousand tokens
UPLOAD_TIMEOUT_SECONDS = 300.0
POLL_SECONDS = 2.0
QUOTA_STATUS = 429
RETRY_WAIT_SECONDS = (20, 60, 120)  # after a server error (5xx); then the run fails

PROMPT = """\
Transcribe the speech in this audio recording verbatim, in English.

Return a JSON array of segments in time order. Each segment is one sentence, or
at most about 20 words: split longer sentences. Each has `start_seconds` and
`end_seconds` (seconds from the start of the recording) and `text`.

Rules:
- Write every word that is spoken, in the order it is spoken. Do not summarize,
  paraphrase, shorten, correct the speaker's grammar or reorder words.
- Keep repetitions, false starts and words like "you know", "like", "so" and
  "I mean" as a careful human transcriber would; do not invent words that are
  not spoken.
- Spell technical terms, commands and identifiers the way they are written in
  code or in product names (for example a spoken "slash theme" is `/theme`).
- Write numbers as digits.
- Cover the whole recording, from the first word to the last."""

GLOSSARY_CONTEXT = """

Terms that may occur in this recording, for spelling only. Do not add a term that
is not spoken:
{terms}"""


class GeminiSegment(BaseModel):
    """One segment of the model's answer; the shape asked for in the response schema."""

    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    text: str


ANSWER = TypeAdapter(list[GeminiSegment])


def build_prompt(glossary: Glossary | None) -> str:
    if glossary is None:
        return PROMPT
    return PROMPT + GLOSSARY_CONTEXT.format(terms="\n".join(f"- {e.term}" for e in glossary.entries))


def parse_answer(text: str | None) -> list[GeminiSegment]:
    """The model's JSON answer -> segments. Anything malformed is a ValueError; nothing is repaired."""
    if not text or not text.strip():
        raise ValueError("Gemini returned an empty answer")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Gemini returned malformed JSON ({e})") from None
    try:
        segments = ANSWER.validate_python(data)
    except ValidationError as e:
        raise ValueError(f"Gemini's answer does not have the requested shape: {e}") from None
    for number, s in enumerate(segments, start=1):
        if s.end_seconds < s.start_seconds:
            raise ValueError(f"Gemini segment {number}: end {s.end_seconds} is before start "
                             f"{s.start_seconds}")
    return segments


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def cost_at_paid_prices(model: str, prompt_tokens: int, output_tokens: int) -> float | None:
    if model not in PRICES_PER_MTOK:
        return None
    price_in, price_out = PRICES_PER_MTOK[model]
    return round((prompt_tokens * price_in + output_tokens * price_out) / 1e6, 6)


def _describe(error: Exception) -> str:
    """`503 UNAVAILABLE` for an SDK API error; the class name for anything else."""
    code, status = getattr(error, "code", None), getattr(error, "status", None)
    return " ".join(str(x) for x in (code, status) if x) or type(error).__name__


def _is_quota_error(error: Exception, errors_module) -> bool:
    return isinstance(error, errors_module.ClientError) and getattr(error, "code", None) == QUOTA_STATUS


class GeminiError(RuntimeError):
    """A Gemini call failed; `model` is the model that was asked."""

    def __init__(self, message: str, model: str):
        super().__init__(message)
        self.model = model
        self.failed_attempts: list[dict] = []  # the retried attempts before this error


class GeminiUnavailable(GeminiError):
    """Overloaded or rate-limited past the retries (5xx, or a per-minute 429): another model may work."""


class GeminiQuotaExhausted(GeminiError):
    """A daily quota is used up or billing is required: no model of this key will work today."""


def is_daily_quota_or_billing(error: Exception) -> bool:
    """
    True for a 429 whose details name a per-day quota (`...PerDay...`), or any
    client error that mentions billing without being a 429 (e.g. 400
    FAILED_PRECONDITION "billing not enabled"). A plain 429 is a per-minute rate
    limit, which passes after a wait. The free tier's 429 message always says
    "check your plan and billing details", so "billing" alone does not mark a 429.
    """
    text = f"{error} {getattr(error, 'message', '') or ''} {getattr(error, 'details', '') or ''}"
    code = getattr(error, "code", None)
    if code == QUOTA_STATUS:
        return "perday" in text.replace(" ", "").lower()
    return code is not None and 400 <= int(code) < 500 and "billing" in text.lower()


def classify_error(error: Exception, errors_module, model: str) -> GeminiError:
    """The SDK error as GeminiQuotaExhausted, GeminiUnavailable or a plain GeminiError."""
    message = f"Gemini {model} failed: {_describe(error)}"
    if is_daily_quota_or_billing(error):
        return GeminiQuotaExhausted(message + " (daily quota used up or billing required)", model)
    if isinstance(error, errors_module.ServerError) or _is_quota_error(error, errors_module):
        return GeminiUnavailable(message, model)
    return GeminiError(message, model)


class GeminiTranscriber:
    """Transcriber backed by a Gemini model through the Files API and structured output."""

    language = "en"

    def __init__(self, model: str | None = None, fallback_model: str | None = None,
                 glossary_prompt: bool = False, tier: str | None = None, quota_fallback: bool = True):
        """
        quota_fallback: on a 429, retry once with `fallback_model` (the single-call
        engine, A4). Off for the chunked engine, which retries a per-minute 429 on
        the same model after RETRY_WAIT_SECONDS and chooses the next model itself.
        """
        self.model = model or config.gemini_model()
        self.fallback_model = fallback_model or config.gemini_fallback_model()
        self.quota_fallback = quota_fallback
        self.glossary_prompt = glossary_prompt
        self.tier = tier or config.gemini_tier()
        if self.tier not in ("free", "paid"):
            raise ValueError(f"GEMINI_TIER must be free or paid, got {self.tier!r}")
        self.name = f"gemini {self.model}"

    def _upload(self, client, types, audio_path: Path):
        uploaded = client.files.upload(
            file=str(audio_path), config=types.UploadFileConfig(mime_type="audio/wav",
                                                                 display_name=audio_path.name))
        deadline = time.monotonic() + UPLOAD_TIMEOUT_SECONDS
        while uploaded.state == types.FileState.PROCESSING:
            if time.monotonic() > deadline:
                raise RuntimeError(f"Gemini file {uploaded.name} still processing after "
                                   f"{UPLOAD_TIMEOUT_SECONDS:.0f} s")
            time.sleep(POLL_SECONDS)
            uploaded = client.files.get(name=uploaded.name)
        if uploaded.state == types.FileState.FAILED:
            raise RuntimeError(f"Gemini could not process the uploaded file {uploaded.name}: "
                               f"{uploaded.error}")
        return uploaded

    def _generate(self, client, types, model: str, uploaded, prompt: str):
        response = client.models.generate_content(
            model=model,
            contents=[uploaded, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=ANSWER.json_schema(),
                max_output_tokens=MAX_OUTPUT_TOKENS,
            ),
        )
        candidates = response.candidates or []
        if not candidates:
            feedback = getattr(response, "prompt_feedback", None)
            raise RuntimeError(f"Gemini {model} returned no answer (prompt feedback: {feedback})")
        reason = candidates[0].finish_reason
        if reason != types.FinishReason.STOP:
            raise RuntimeError(f"Gemini {model} stopped with {reason}, not STOP; the transcript may be "
                               "incomplete")
        return response

    def _generate_with_retries(self, client, types, errors, model: str, uploaded, prompt: str,
                               failures: list[dict]):
        """
        `_generate`, retried after a server error (HTTP 5xx, e.g. 503 "high demand")
        with the waits in RETRY_WAIT_SECONDS. Each failed attempt is appended to
        `failures`. Client errors (4xx) are raised at once, except a per-minute
        rate limit (429 without a daily quota) when `quota_fallback` is off: it is
        retried like a server error.
        """
        for attempt, wait in enumerate((*RETRY_WAIT_SECONDS, None), start=1):
            logger.info("Gemini %s: transcribing (attempt %d)", model, attempt)
            try:
                return self._generate(client, types, model, uploaded, prompt)
            except errors.APIError as e:
                rate_limited = _is_quota_error(e, errors) and not is_daily_quota_or_billing(e)
                retryable = isinstance(e, errors.ServerError) or (not self.quota_fallback and rate_limited)
                if not retryable:
                    raise
                failures.append({"model": model, "attempt": attempt, "error": _describe(e)})
                if wait is None:
                    raise
                logger.warning("Gemini %s: %s; waiting %d s before retrying", model, _describe(e), wait)
                time.sleep(wait)
        raise AssertionError("unreachable")

    def transcribe(self, audio_path: Path, glossary: Glossary | None) -> Transcript:
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise FileNotFoundError(f"No such file: {audio_path}")
        if self.glossary_prompt and glossary is None:
            raise ValueError("glossary_prompt is on but no glossary was given")
        key = config.require_key("GEMINI_API_KEY")
        genai = importlib.import_module("google.genai")
        types = importlib.import_module("google.genai.types")
        errors = importlib.import_module("google.genai.errors")
        audio_seconds = wav_seconds(audio_path)
        prompt = build_prompt(glossary if self.glossary_prompt else None)

        client = genai.Client(api_key=key)
        started = time.perf_counter()
        logger.info("Gemini: uploading %s (%.0f s of audio)", audio_path.name, audio_seconds)
        uploaded = self._upload(client, types, audio_path)
        upload_seconds = time.perf_counter() - started
        model, fallback_reason, failures = self.model, None, []

        def generate(name: str):
            return self._generate_with_retries(client, types, errors, name, uploaded, prompt, failures)

        try:
            try:
                response = generate(model)
            except errors.APIError as e:
                if not (self.quota_fallback and _is_quota_error(e, errors)):
                    error = classify_error(e, errors, model)
                    error.failed_attempts = list(failures)
                    raise error from e
                fallback_reason = f"{model}: {_describe(e)}"
                logger.warning("Gemini %s rejected for quota; retrying with %s", model, self.fallback_model)
                model = self.fallback_model
                try:
                    response = generate(model)
                except errors.APIError as e2:
                    raise classify_error(e2, errors, model) from e2
        finally:
            try:
                client.files.delete(name=uploaded.name)
            except Exception as e:  # the service deletes it after 48 hours anyway
                logger.warning("Could not delete Gemini file %s: %s", uploaded.name, type(e).__name__)
        wall = time.perf_counter() - started

        answer = parse_answer(response.text)
        segments = [Segment(id=n, start=s.start_seconds, end=s.end_seconds, text=s.text.strip(),
                            confidence=None) for n, s in enumerate(answer, start=1)]
        segments, dropped = drop_wordless(segments)
        past_end = sum(1 for s in segments if s.end > audio_seconds + 1.0)

        usage = response.usage_metadata
        prompt_tokens = usage.prompt_token_count or 0
        candidate_tokens = usage.candidates_token_count or 0
        thought_tokens = usage.thoughts_token_count or 0
        paid = cost_at_paid_prices(model, prompt_tokens, candidate_tokens + thought_tokens)
        logger.info("Gemini %s: %d segments in %.1f s; %d prompt, %d output, %d thinking tokens", model,
                    len(segments), wall, prompt_tokens, candidate_tokens, thought_tokens)

        return Transcript(
            segments=segments,
            language=self.language,
            source=f"gemini {model}",
            meta={
                "engine": "gemini",
                "engine_version": getattr(genai, "__version__", "unknown"),
                "model": model,
                "model_version": getattr(response, "model_version", None),
                "model_requested": self.model,
                "fallback_reason": fallback_reason,
                "failed_attempts": failures,
                "glossary_prompt": self.glossary_prompt,
                "prompt": prompt,
                "request_id": getattr(response, "response_id", None),
                "input": audio_path.name,
                "audio_seconds": round(audio_seconds, 3),
                "timestamps": "model-estimated",
                "segments_dropped_wordless": dropped,
                "segments_ending_past_audio": past_end,
                "upload_seconds": round(upload_seconds, 1),
                "wall_seconds": round(wall, 1),
                "realtime_factor": round(audio_seconds / wall, 3) if wall > 0 else None,
                "tokens": {"prompt": prompt_tokens, "output": candidate_tokens, "thinking": thought_tokens,
                           "total": usage.total_token_count},
                "tier": self.tier,
                "cost_usd": 0.0 if self.tier == "free" else paid,
                "cost_usd_at_paid_prices": paid,
                "price_checked": PRICES_CHECKED,
            },
        )
