"""
Whisper + Google Translate baseline (evaluation tooling, not part of `pipeline-v3`).

    raw Whisper transcript (no glossary, no correction)
      -> sentence units (units.make_units, as the pipeline translates)
      -> Google Cloud Translation API, Basic edition (v2), one text per unit
      -> <lang>.json (the units with the translated text, English word timings kept)
      -> <name>.<lang>.srt + .ass (subtitles.write_srt, the pipeline's subtitle layout)

So the baseline differs from the pipeline only where the comparison is meant to
be: the speech recognition (raw faster-whisper large-v3 instead of AssemblyAI
with key terms and Claude correction) and the translation (Google Translate
instead of Claude); units and subtitle layout are the same code.

The API key comes from `GOOGLE_TRANSLATE_API_KEY` (registered in
`secret_registry`, never printed or logged) and is sent in the `X-goog-api-key`
header, not in the URL. The request uses `format: text`, so the answer has no
HTML entities. Billing is per character of input: the first 500,000 characters
per month are free (Basic edition); `Result.characters` counts what was sent.
Stdlib HTTP only: adding a dependency would change the frozen `uv.lock`.
"""

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from talk_autopsy import subtitles, units
from talk_autopsy.segments import Segment, Transcript, load_transcript, save_transcript

VERSION = "whisper+google-translate-v2"
KEY_ENV_VAR = "GOOGLE_TRANSLATE_API_KEY"
ENDPOINT = "https://translation.googleapis.com/language/translate/v2"
MAX_TEXTS_PER_REQUEST = 128  # the API's limit on q entries
MAX_CHARS_PER_REQUEST = 5_000  # Google's recommended maximum request size
FREE_CHARS_PER_MONTH = 500_000
USD_PER_MILLION_CHARS = 20.0  # Basic edition, beyond the free tier

SETUP_STEPS = """\
GOOGLE_TRANSLATE_API_KEY is not set, so the Whisper + Google Translate baseline was not run.
To run it (free tier: 500,000 characters per month; T2 + T6 need about 22,000):
  1. Google Cloud console, project talk-autopsy: APIs & Services -> Library ->
     "Cloud Translation API" -> Enable.
  2. APIs & Services -> Credentials -> Create credentials -> API key. Edit the key:
     API restrictions -> Restrict key -> select only "Cloud Translation API" -> Save.
  3. Put it in the project's .env:  GOOGLE_TRANSLATE_API_KEY=<the key>
  4. Run: uv run python scripts/google_translate_baseline.py"""

Caller = Callable[[list[str], str, str], list[str]]  # texts, source language, target language -> texts


class GoogleTranslateError(RuntimeError):
    """The Translation API refused or answered something unusable (never carries the key)."""


def api_key() -> str | None:
    """The key from the environment (entry points load .env first); None if unset or blank."""
    return os.getenv(KEY_ENV_VAR, "").strip() or None


def batches(texts: list[str], max_texts: int = MAX_TEXTS_PER_REQUEST,
            max_chars: int = MAX_CHARS_PER_REQUEST) -> list[list[int]]:
    """Indices of `texts` grouped into requests within both limits, in order; a long text goes alone."""
    groups: list[list[int]] = []
    current: list[int] = []
    size = 0
    for index, text in enumerate(texts):
        if current and (len(current) == max_texts or size + len(text) > max_chars):
            groups.append(current)
            current, size = [], 0
        current.append(index)
        size += len(text)
    if current:
        groups.append(current)
    return groups


def http_caller(key: str, timeout: float = 60.0,
                urlopen: Callable = urllib.request.urlopen) -> Caller:
    """A caller for the real API (v2 `translate`, POST with a JSON body)."""

    def call(texts: list[str], source: str, target: str) -> list[str]:
        body = json.dumps({"q": texts, "source": source, "target": target, "format": "text"}).encode("utf-8")
        request = urllib.request.Request(
            ENDPOINT, data=body, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8", "X-goog-api-key": key})
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise GoogleTranslateError(f"Translation API HTTP {e.code}: {_error_message(e)}") from None
        except urllib.error.URLError as e:
            raise GoogleTranslateError(f"Translation API not reachable: {e.reason}") from None
        try:
            return [t["translatedText"] for t in payload["data"]["translations"]]
        except (KeyError, TypeError):
            raise GoogleTranslateError("Translation API answer has no data.translations") from None

    return call


def _error_message(error: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(error.read().decode("utf-8"))["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return error.reason or "no message"


@dataclass(frozen=True)
class Result:
    translated: Transcript
    units: int
    characters: int  # characters sent (what Google bills)
    requests: int

    @property
    def cost_usd_beyond_free_tier(self) -> float:
        return self.characters * USD_PER_MILLION_CHARS / 1_000_000


def translate_units(unit_transcript: Transcript, target_lang: str, caller: Caller,
                    source_lang: str = "en") -> Result:
    """Each unit's text through `caller` in batches; ids, times, words and source ids are kept."""
    todo = [s for s in unit_transcript.segments if s.text.strip()]
    texts = [s.text.strip() for s in todo]
    translated: dict[int, str] = {}
    groups = batches(texts)
    for group in groups:
        answer = caller([texts[i] for i in group], source_lang, target_lang)
        if len(answer) != len(group):
            raise GoogleTranslateError(f"sent {len(group)} texts, got {len(answer)} translations")
        for i, text in zip(group, answer, strict=True):
            translated[todo[i].id] = text.strip()
    segments = [Segment(id=s.id, start=s.start, end=s.end, text=translated.get(s.id, s.text),
                        words=s.words, confidence=None, source_ids=s.source_ids)
                for s in unit_transcript.segments]
    characters = sum(len(t) for t in texts)
    meta = dict(unit_transcript.meta)
    meta["translation"] = {"baseline": VERSION, "engine": "Google Cloud Translation API Basic (v2)",
                           "source_lang": source_lang, "target_lang": target_lang,
                           "characters": characters, "requests": len(groups)}
    transcript = Transcript(segments=segments, language=target_lang,
                            source=f"{unit_transcript.source} + google-translate", meta=meta)
    return Result(transcript, len(todo), characters, len(groups))


def run(raw_path: Path, out_dir: Path, name: str, target_lang: str, caller: Caller) -> tuple[Result, Path]:
    """Raw transcript -> units -> translation -> `<out_dir>/<lang>.json` and `<name>.<lang>.srt/.ass`."""
    unit_transcript = units.make_units(load_transcript(raw_path))
    result = translate_units(unit_transcript, target_lang, caller)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    save_transcript(result.translated, out_dir / f"{target_lang}.json")
    srt = out_dir / f"{name}.{target_lang}.srt"
    subtitles.write_srt(result.translated, srt)
    return result, srt
