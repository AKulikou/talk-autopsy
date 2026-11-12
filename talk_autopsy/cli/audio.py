"""Audio and speech recognition: `cut`, `transcribe`, `align`."""

from pathlib import Path
from typing import Annotated

import typer

from talk_autopsy import align as align_module
from talk_autopsy import config, ingest
from talk_autopsy.cli.common import fail, log_to_stderr, print_progress, timecode
from talk_autopsy.config import ConfigurationError
from talk_autopsy.glossary import GlossaryError, load_glossary
from talk_autopsy.segments import load_transcript, save_transcript
from talk_autopsy.stt import assemblyai
from talk_autopsy.stt.assemblyai import AssemblyAITranscriber
from talk_autopsy.stt.chunked import ChunkedGeminiTranscriber
from talk_autopsy.stt.faster_whisper import FasterWhisperTranscriber
from talk_autopsy.stt.gemini import GeminiTranscriber


def cut(
    source: Annotated[Path, typer.Argument(help="Video or audio file.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="WAV file to write.")],
    start: Annotated[str | None, typer.Option(help="SS, MM:SS or HH:MM:SS. Default: the beginning.")] = None,
    end: Annotated[str | None, typer.Option(help="SS, MM:SS or HH:MM:SS. Default: the end.")] = None,
) -> None:
    """Extract 16 kHz mono WAV audio from SOURCE, optionally only from --start to --end."""
    begin, finish = timecode(start), timecode(end)
    try:
        ingest.extract_audio(source, out, begin, finish)
        seconds = ingest.probe(out)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        fail(str(e))
    typer.echo(f"Wrote {out} ({seconds:.1f} s, {ingest.SAMPLE_RATE} Hz mono)")


ENGINES = ("faster-whisper", "assemblyai", "gemini", "gemini+align")


def make_engine(engine: str, model: str | None, glossary_prompt: bool, threads: int = 0,
                cache_dir: Path | None = None):
    """
    The Transcriber for an --engine name; `model` overrides the engine's default model
    (for gemini+align: the first of the model list). `cache_dir` holds the chunk cache
    of gemini+align.
    """
    if engine == "faster-whisper":
        return FasterWhisperTranscriber(model_size=model or "large-v3", glossary_prompt=glossary_prompt,
                                        cpu_threads=threads)
    if engine == "assemblyai":
        return AssemblyAITranscriber(model=model or assemblyai.MODEL, glossary_prompt=glossary_prompt)
    if engine == "gemini":
        return GeminiTranscriber(model=model, glossary_prompt=glossary_prompt)
    if engine == "gemini+align":
        models = [model, *[m for m in config.gemini_models() if m != model]] if model else None
        inner = ChunkedGeminiTranscriber(models=models, glossary_prompt=glossary_prompt, cache_dir=cache_dir,
                                         progress=print_progress)
        return align_module.AlignedTranscriber(inner, "gemini+align", progress=print_progress)
    raise AssertionError(engine)  # _check_engine admits only ENGINES


def _check_engine(engine: str) -> str:
    name = engine.strip().lower()
    if name not in ENGINES:
        raise typer.BadParameter(f"unknown engine {engine!r}; known: {', '.join(ENGINES)}",
                                 param_hint="--engine")
    return name


def transcribe(
    audio: Annotated[Path, typer.Argument(help="16 kHz mono WAV (from `talk-autopsy cut`).")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Transcript JSON to write.")],
    engine: Annotated[str, typer.Option(help=f"Speech recognition engine: {' | '.join(ENGINES)}.")]
    = "faster-whisper",
    model: Annotated[str | None, typer.Option(
        help="Model of the engine. Default: large-v3 (faster-whisper), "
             "universal-3-5-pro (assemblyai), GEMINI_MODEL (gemini), the first of GEMINI_MODELS "
             "(gemini+align).")] = None,
    glossary: Annotated[Path | None, typer.Option(help="Glossary Markdown table of the fragment.")] = None,
    glossary_prompt: Annotated[bool, typer.Option(
        "--glossary-prompt",
        help="Pass the glossary terms to the engine "
             "(Whisper prompt, AssemblyAI keyterms, Gemini context).")] = False,
    threads: Annotated[int, typer.Option(
        min=0, help="CPU threads for faster-whisper; 0 = its default (4).")] = 0,
) -> None:
    """Speech recognition of AUDIO: faster-whisper on the CPU, or a cloud engine (the audio is uploaded)."""
    engine = _check_engine(engine)
    if threads and engine != "faster-whisper":
        raise typer.BadParameter("--threads applies to faster-whisper only", param_hint="--threads")
    if glossary_prompt and glossary is None:
        raise typer.BadParameter("--glossary-prompt needs --glossary", param_hint="--glossary-prompt")
    if not audio.is_file():
        fail(f"No such file: {audio}")
    try:
        terms = load_glossary(glossary) if glossary is not None else None
    except (FileNotFoundError, GlossaryError) as e:
        fail(str(e))
    log_to_stderr()
    transcriber = make_engine(engine, model, glossary_prompt, threads, cache_dir=out.parent / "chunks")
    try:
        transcript = transcriber.transcribe(audio, terms)
    except (FileNotFoundError, ValueError, RuntimeError, ConfigurationError) as e:
        fail(str(e))
    if glossary is not None:
        transcript.meta["glossary"] = glossary.name
    save_transcript(transcript, out)
    meta = transcript.meta
    audio_seconds = meta.get("audio_seconds")
    cost = meta.get("cost_usd")
    typer.echo(f"Wrote {out}: {len(transcript.segments)} segments, "
               f"{audio_seconds:.0f} s of audio in {meta['wall_seconds']:.0f} s "
               f"(realtime factor {meta.get('realtime_factor')}; "
               f"cost {'n/a' if cost is None else f'{cost:.4f} USD'})")


def align_command(
    transcript: Annotated[Path, typer.Argument(help="Transcript JSON (any engine; times may be estimates).")],
    audio: Annotated[Path, typer.Argument(help="The 16 kHz mono WAV the transcript was made from.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Aligned transcript JSON to write.")],
) -> None:
    """Forced alignment: re-time every word of TRANSCRIPT on AUDIO (needs `uv sync --extra align`)."""
    if not audio.is_file():
        fail(f"No such file: {audio}")
    log_to_stderr()
    try:
        source = load_transcript(transcript)
        result = align_module.align(source, audio, align_module.Wav2Vec2Emitter())
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        fail(str(e))
    save_transcript(result, out)
    a = result.meta["alignment"]
    typer.echo(f"Wrote {out}: {a['words']} words in {len(result.segments)} segments, "
               f"{a['unplaced_words']} unplaced, {a['audio_seconds']:.0f} s of audio "
               f"in {a['wall_seconds']:.0f} s "
               f"(realtime factor {a['realtime_factor']})")
