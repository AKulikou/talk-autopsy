"""
Cloud engine: AssemblyAI pre-recorded speech recognition.

    audio.wav -> AssemblyAI (upload, transcribe) -> raw.json (Transcript with word timings)

Checked against the AssemblyAI docs (model selection, keyterms prompting,
sentences export, pricing) and the installed SDK `assemblyai` 1.8.2 on
2026-11-09:

- Best-accuracy model: Universal-3.5 Pro, API value `universal-3-5-pro`,
  selected with `speech_models=[...]` (the singular `speech_model` is
  deprecated). Only this one model is requested: the service's default list
  falls back to `universal-2`, which would silently change the engine being
  measured. The model that actually ran (`speech_model_used`) goes to meta.
- Vocabulary boost: `keyterms_prompt`, a list of phrases of at most 6 words.
  Off by default; on with `glossary_prompt`, as in the Whisper engine
  (hypothesis A2). Limit (keyterms docs, read 2026-11-11: "up to 1,000 words or
  phrases (maximum 6 words per phrase)"; "Each word in a multi-word phrase
  counts towards the 1000 keyword limit"; the docs do not say what happens
  above it): at most 1,000 words in all. `select_keyterms` truncates to that,
  the user's terms first, then the auto-glossary's by frequency in the first
  transcript, and reports what it dropped; it never raises.
- English (`language_code="en"`), speaker labels off.
- Word and sentence times are integer milliseconds; `audio_duration` is in
  seconds. The response has no separate billed-duration field: billing is the
  submitted audio duration, so `audio_seconds` doubles as the billed duration.
- Price: $0.21 per hour of audio for Universal-3.5 Pro (pricing page, header
  "Last updated: 2026-10-27"); the pricing page also lists +$0.05 per hour for
  keyterms, which the docs do not confirm. New accounts get $50 of free credit,
  so `cost_usd` is the list price the run used up, not money charged.

Segments are the service's sentences (`get_sentences`). Without sentences the
words are split at pauses longer than 0.7 s or after 25 words. Segment
`confidence` is the mean of its word confidences; each word keeps its own.

The SDK is imported when a run starts, not at import of this module, so tests
can replace it with a fake.
"""

import importlib
import logging
import statistics
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from talk_autopsy import config, metrics
from talk_autopsy.glossary import Glossary
from talk_autopsy.segments import Segment, Transcript, Word
from talk_autopsy.stt.base import drop_wordless

logger = logging.getLogger(__name__)

MODEL = "universal-3-5-pro"
KEYTERM_MAX_WORDS = 6
KEYTERM_MAX_TOTAL_WORDS = 1000  # every word of a phrase counts toward the service's 1,000 limit
HEARD_AS = "heard as: "  # glossary_auto writes the mis-recognized forms last in the notes

PRICE_PER_HOUR_USD = 0.21  # Universal-3.5 Pro, pre-recorded, pricing page dated 2026-10-27
KEYTERMS_PRICE_PER_HOUR_USD = 0.05  # pricing page only; not confirmed by the docs

PAUSE_SPLIT_SECONDS = 0.7
MAX_SEGMENT_WORDS = 25


@dataclass
class KeytermSelection:
    terms: list[str]  # what goes to keyterms_prompt, in priority order
    dropped: list[str] = field(default_factory=list)  # left out: the total word limit was reached
    too_long: list[str] = field(default_factory=list)  # left out: over KEYTERM_MAX_WORDS words

    @property
    def words(self) -> int:
        return sum(len(t.split()) for t in self.terms)


def _heard_as(notes: str) -> list[str]:
    if HEARD_AS not in notes:
        return []
    return [f.strip() for f in notes.split(HEARD_AS, 1)[1].split(",") if f.strip()]


def select_keyterms(glossary: Glossary, user_terms: Iterable[str] = (), transcript: str = "",
                    max_words: int = KEYTERM_MAX_TOTAL_WORDS) -> KeytermSelection:
    """
    The glossary terms for `keyterms_prompt` within the service's limit.

    Terms over `KEYTERM_MAX_WORDS` words are left out (not cut). The rest are
    ordered: the user's terms first (`user_terms`, in glossary order), then the
    other terms by how often they occur in `transcript` (the first pass), the
    term itself plus the forms its notes list as "heard as" (case-insensitive),
    most frequent first, ties in glossary order. Terms are taken in that order
    while the total word count stays within `max_words`; a term that does not
    fit is dropped and later, shorter terms may still fit. Nothing raises.
    """
    user = {t.casefold() for t in user_terms}
    seen, candidates, too_long = set(), [], []
    for entry in glossary.entries:
        key = entry.term.casefold()
        if key in seen:
            continue
        seen.add(key)
        if len(entry.term.split()) > KEYTERM_MAX_WORDS:
            too_long.append(entry.term)
        else:
            candidates.append(entry)
    lowered = transcript.lower()

    def frequency(entry) -> int:
        forms = [entry.term, *_heard_as(entry.notes)]
        return sum(len(found) for found in metrics.term_occurrences(lowered, [f.lower() for f in forms])
                   ) if lowered and forms else 0

    first = [e for e in candidates if e.term.casefold() in user]
    rest = [e for e in candidates if e.term.casefold() not in user]
    rest = [e for _, e in sorted(enumerate(rest), key=lambda pair: (-frequency(pair[1]), pair[0]))]
    selection = KeytermSelection(terms=[], too_long=too_long)
    total = 0
    for entry in first + rest:
        n = len(entry.term.split())
        if total + n <= max_words:
            selection.terms.append(entry.term)
            total += n
        else:
            selection.dropped.append(entry.term)
    if too_long:
        logger.info("Keyterms: left out %d term(s) over %d words: %s", len(too_long), KEYTERM_MAX_WORDS,
                    ", ".join(too_long))
    if selection.dropped:
        logger.warning("Keyterms: the service takes at most %d words; dropped %d term(s): %s", max_words,
                       len(selection.dropped), ", ".join(selection.dropped))
    return selection


def keyterms(glossary: Glossary) -> list[str]:
    """Glossary terms for `keyterms_prompt` in glossary order, truncated to the limit (`select_keyterms`)."""
    return select_keyterms(glossary).terms


def _word(w) -> Word:
    return Word(start=w.start / 1000, end=w.end / 1000, text=w.text, probability=w.confidence)


def _segment(number: int, text: str, words: list[Word]) -> Segment:
    confidences = [w.probability for w in words if w.probability is not None]
    return Segment(id=number, start=words[0].start, end=words[-1].end, text=text, words=words,
                   confidence=statistics.fmean(confidences) if confidences else None)


def split_words(words: list[Word], pause: float = PAUSE_SPLIT_SECONDS,
                max_words: int = MAX_SEGMENT_WORDS) -> list[list[Word]]:
    """
    Group words into segments: a new segment starts after a pause longer than
    `pause` seconds or when the current one has `max_words` words.
    """
    groups: list[list[Word]] = []
    for word in words:
        if groups and len(groups[-1]) < max_words and word.start - groups[-1][-1].end <= pause:
            groups[-1].append(word)
        else:
            groups.append([word])
    return groups


def segments_from_response(sentences, words) -> tuple[list[Segment], str]:
    """
    Segments from the service's sentences, or from its words split at pauses when
    there are no sentences. Returns the segments and how they were made
    ("sentences" or "pauses").
    """
    if sentences:
        segments = [_segment(n, s.text, [_word(w) for w in s.words])
                    for n, s in enumerate((s for s in sentences if s.words), start=1)]
        return segments, "sentences"
    groups = split_words([_word(w) for w in words or []])
    return [_segment(n, " ".join(w.text for w in g), g) for n, g in enumerate(groups, start=1)], "pauses"


class AssemblyAITranscriber:
    """Transcriber backed by AssemblyAI Universal-3.5 Pro."""

    language = "en"

    def __init__(self, model: str = MODEL, glossary_prompt: bool = False, user_terms: Iterable[str] = (),
                 first_pass: str = ""):
        self.model = model
        self.glossary_prompt = glossary_prompt
        self.user_terms = list(user_terms)  # key-term priority (select_keyterms)
        self.first_pass = first_pass  # text of the first transcript: key-term frequency
        self.name = f"assemblyai {model}"

    def transcribe(self, audio_path: Path, glossary: Glossary | None) -> Transcript:
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise FileNotFoundError(f"No such file: {audio_path}")
        if self.glossary_prompt and glossary is None:
            raise ValueError("glossary_prompt is on but no glossary was given")
        key = config.require_key("ASSEMBLYAI_API_KEY")
        aai = importlib.import_module("assemblyai")
        aai.settings.api_key = key

        selection = (select_keyterms(glossary, self.user_terms, self.first_pass) if self.glossary_prompt
                     else None)
        terms = selection.terms if selection else None
        if self.glossary_prompt and not terms:
            raise ValueError("glossary_prompt is on but the glossary has no usable term")
        settings = {"speech_models": [self.model], "language_code": self.language, "speaker_labels": False}
        if terms:
            settings["keyterms_prompt"] = terms
        logger.info("AssemblyAI %s: uploading and transcribing %s (keyterms: %s)", self.model,
                    audio_path.name, len(terms) if terms else "none")

        started = time.perf_counter()
        transcript = aai.Transcriber().transcribe(str(audio_path), aai.TranscriptionConfig(**settings))
        if transcript.status == aai.TranscriptStatus.error:
            raise RuntimeError(f"AssemblyAI transcription {transcript.id} failed: {transcript.error}")
        if transcript.status != aai.TranscriptStatus.completed:
            raise RuntimeError(f"AssemblyAI transcription {transcript.id} ended with status "
                               f"{transcript.status}")
        sentences = transcript.get_sentences()
        wall = time.perf_counter() - started

        segments, segmented_by = segments_from_response(sentences, transcript.words)
        segments, dropped = drop_wordless(segments)
        model_used = getattr(transcript, "speech_model_used", None) or self.model
        if model_used != self.model:
            logger.warning("AssemblyAI ran %s, not the requested %s", model_used, self.model)
        audio_seconds = transcript.audio_duration
        cost = None
        if audio_seconds is not None:
            rate = PRICE_PER_HOUR_USD + (KEYTERMS_PRICE_PER_HOUR_USD if terms else 0.0)
            cost = round(audio_seconds / 3600 * rate, 4)
        logger.info("AssemblyAI: %d segments (%s) in %.1f s", len(segments), segmented_by, wall)

        return Transcript(
            segments=segments,
            language=self.language,
            source=f"assemblyai {model_used}",
            meta={
                "engine": "assemblyai",
                "engine_version": getattr(aai, "__version__", "unknown"),
                "model": model_used,
                "model_requested": self.model,
                "glossary_prompt": self.glossary_prompt,
                "keyterms": terms,
                "keyterms_dropped": selection.dropped if selection else [],
                "keyterms_too_long": selection.too_long if selection else [],
                "request_id": transcript.id,
                "input": audio_path.name,
                "audio_seconds": audio_seconds,
                # The API has no billed-duration field; billing is the submitted audio duration.
                "billed_seconds": audio_seconds,
                "segmented_by": segmented_by,
                "segments_dropped_wordless": dropped,
                "timestamps": "measured",
                "wall_seconds": round(wall, 1),
                "realtime_factor": round(audio_seconds / wall, 3) if audio_seconds and wall > 0 else None,
                "cost_usd": cost,  # list price; covered by the free credit while it lasts
                "price_checked": "2026-11-09",
            },
        )
