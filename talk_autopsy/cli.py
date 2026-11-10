"""
Command line: `cut`, `transcribe` (faster-whisper or a cloud engine), `align`,
`fix`, `translate`, `srt`, `draft`, `ref-from-draft`, `adjudicate`, `resolve`,
`judge`, `judge-compare`, `eval` and `run` (the pipeline, pipeline.py).

Only argument parsing and printing live here; the stages are plain functions in
the package, so a web UI can call them the same way later.
"""

import json
import logging
import re
import sys
from pathlib import Path
from typing import Annotated

import typer

from talk_autopsy import adjudicate as consensus_module
from talk_autopsy import align as align_module
from talk_autopsy import (
    blocks,
    config,
    correct,
    evaluation,
    glossary_auto,
    ingest,
    judge,
    pipeline,
    proofread,
    subtitles,
    translate,
)
from talk_autopsy.config import ConfigurationError
from talk_autopsy.glossary import GlossaryError, load_glossary, parse_glossary
from talk_autopsy.segments import load_transcript, save_transcript
from talk_autopsy.stt import assemblyai
from talk_autopsy.stt.assemblyai import AssemblyAITranscriber
from talk_autopsy.stt.chunked import ChunkedGeminiTranscriber
from talk_autopsy.stt.faster_whisper import FasterWhisperTranscriber
from talk_autopsy.stt.gemini import GeminiTranscriber

app = typer.Typer(
    help="English technical talks to subtitles in a target language.",
    no_args_is_help=True,
    add_completion=False,
)

# Pipeline order; `run --steps` takes any subset and keeps this order.
STEPS: dict[str, str] = {
    "stt": "speech recognition ({engine}; first pass, compressed audio upload) -> raw.json",
    "glossary": "auto-glossary ({helper}) merged with --glossary -> glossary.md",
    "stt2": "speech recognition with the glossary as key terms -> raw2.json",
    "align": "forced alignment where times are model estimates -> aligned.json",
    "fix": "Claude correction ({correction}) -> fixed.json",
    "translate": "Claude translation ({translation}) -> {lang}.json",
    "srt": "subtitles -> .{lang}.srt/.ass and .en.srt/.ass",
    "dub": "dubbed audio (edge-tts + ffmpeg) -> .{lang}.dub.m4a",
}
CLAUDE_STEPS = {"glossary", "fix", "translate"}
DEFAULT_STEPS = "stt,glossary,stt2,align,fix,translate,srt"


def parse_steps(value: str) -> list[str]:
    """Comma-separated step names -> the same steps in pipeline order."""
    names = [name.strip().lower() for name in value.split(",") if name.strip()]
    if not names:
        raise typer.BadParameter("give at least one step")
    unknown = [name for name in names if name not in STEPS]
    if unknown:
        raise typer.BadParameter(f"unknown step(s) {', '.join(unknown)}; known: {', '.join(STEPS)}")
    if len(set(names)) != len(names):
        raise typer.BadParameter(f"a step is listed twice: {value}")
    return [name for name in STEPS if name in names]


def is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def _fail(message: str) -> None:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _timecode(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return ingest.parse_timecode(value)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from None


@app.command()
def cut(
    source: Annotated[Path, typer.Argument(help="Video or audio file.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="WAV file to write.")],
    start: Annotated[str | None, typer.Option(help="SS, MM:SS or HH:MM:SS. Default: the beginning.")] = None,
    end: Annotated[str | None, typer.Option(help="SS, MM:SS or HH:MM:SS. Default: the end.")] = None,
) -> None:
    """Extract 16 kHz mono WAV audio from SOURCE, optionally only from --start to --end."""
    begin, finish = _timecode(start), _timecode(end)
    try:
        ingest.extract_audio(source, out, begin, finish)
        seconds = ingest.probe(out)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        _fail(str(e))
    typer.echo(f"Wrote {out} ({seconds:.1f} s, {ingest.SAMPLE_RATE} Hz mono)")


def _log_to_stderr() -> None:
    """Progress of long stages (model download, transcription) goes to stderr with timestamps."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
                        stream=sys.stderr, force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per Hugging Face request otherwise


ENGINES = ("faster-whisper", "assemblyai", "gemini", "gemini+align")


def _print_progress(event) -> None:
    typer.echo(event.line(), err=True)


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
                                         progress=_print_progress)
        return align_module.AlignedTranscriber(inner, "gemini+align", progress=_print_progress)
    raise AssertionError(engine)  # _check_engine admits only ENGINES


def _check_engine(engine: str) -> str:
    name = engine.strip().lower()
    if name not in ENGINES:
        raise typer.BadParameter(f"unknown engine {engine!r}; known: {', '.join(ENGINES)}",
                                 param_hint="--engine")
    return name


@app.command()
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
        _fail(f"No such file: {audio}")
    try:
        terms = load_glossary(glossary) if glossary is not None else None
    except (FileNotFoundError, GlossaryError) as e:
        _fail(str(e))
    _log_to_stderr()
    transcriber = make_engine(engine, model, glossary_prompt, threads, cache_dir=out.parent / "chunks")
    try:
        transcript = transcriber.transcribe(audio, terms)
    except (FileNotFoundError, ValueError, RuntimeError, ConfigurationError) as e:
        _fail(str(e))
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


ForceOption = Annotated[bool, typer.Option("--force", help="Overwrite OUT if it exists.")]


def _stage_inputs(transcript: Path, glossary: Path | None, context: Path | None):
    try:
        source = load_transcript(transcript)
        terms = load_glossary(glossary) if glossary is not None else parse_glossary("")
    except (FileNotFoundError, ValueError, GlossaryError) as e:
        _fail(str(e))
    return source, terms, blocks.load_context(context)


def _confirm_cost(estimate, max_cost: float) -> None:
    typer.echo(f"Estimate: {estimate.line()}")
    if estimate.usd > max_cost:
        _fail(f"the estimate is above --max-cost {max_cost:.2f} USD; raise it to confirm")


@app.command(name="fix")
def fix_command(
    transcript: Annotated[Path, typer.Argument(help="Raw or aligned transcript JSON.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Corrected transcript JSON to write.")],
    glossary: Annotated[Path | None, typer.Option(help="Glossary Markdown table of the video.")] = None,
    context: Annotated[Path | None, typer.Option(help="meta.json with title, description, chapters.")] = None,
    model: Annotated[str | None, typer.Option(help="Claude model. Default: CORRECTION_MODEL.")] = None,
    prompt: Annotated[str, typer.Option(help=f"Prompt version: {' | '.join(correct.VERSIONS)}.")]
    = correct.PROMPT_VERSION,
    max_cost: Annotated[float, typer.Option(help="Stop if the estimate is above this (USD).")]
    = config.COST_CONFIRMATION_USD,
) -> None:
    """Claude correction of TRANSCRIPT: prints the estimate, then corrects block by block."""
    if prompt not in correct.VERSIONS:
        raise typer.BadParameter(f"unknown prompt {prompt!r}; known: {', '.join(correct.VERSIONS)}",
                                 param_hint="--prompt")
    try:
        config.require_api_key()
    except ConfigurationError as e:
        _fail(str(e))
    source, terms, ctx = _stage_inputs(transcript, glossary, context)
    model = model or config.correction_model()
    _confirm_cost(correct.estimate(source, terms, ctx, model, version=prompt), max_cost)
    _log_to_stderr()
    try:
        result = correct.correct(source, terms, ctx, model=model, version=prompt,
                                 progress=lambda n, total: typer.echo(f"  block {n}/{total}", err=True))
    except (RuntimeError, ValueError, ConfigurationError) as e:
        _fail(str(e))
    save_transcript(result, out)
    c = result.meta["correction"]
    typer.echo(f"Wrote {out}: {c['segments_changed']} of {len(result.segments)} segments changed, "
               f"{len(c['edits'])} edits, {len(c['failed_blocks'])} failed blocks; {c['cost_usd']:.4f} USD "
               f"({c['usage']['input_tokens']} in, {c['usage']['cache_creation_input_tokens']} cache write, "
               f"{c['usage']['cache_read_input_tokens']} cache read, {c['usage']['output_tokens']} out)")


@app.command(name="glossary-auto")
def glossary_auto_command(
    transcript: Annotated[Path, typer.Argument(help="Raw transcript JSON (first speech recognition pass).")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Glossary Markdown table to write.")],
    context: Annotated[str | None, typer.Option(
        help="Video title, description, chapter titles: text, or a file holding it. "
             "A local file's name is not context.")] = None,
    lang: Annotated[str | None, typer.Option(help="Target language code. Default: TARGET_LANG.")] = None,
    model: Annotated[str | None, typer.Option(help="Claude model. Default: HELPER_MODEL.")] = None,
    compare: Annotated[Path | None, typer.Option(
        help="Reference glossary: print coverage (reference terms spelled right) and noise.")] = None,
    max_cost: Annotated[float, typer.Option(help="Stop if the estimate is above this (USD).")]
    = config.COST_CONFIRMATION_USD,
) -> None:
    """Draft the glossary of a video from its first transcript (glossary-auto-v1, helper model)."""
    try:
        config.require_api_key()
        target_lang = config.check_lang(lang if lang is not None else config.target_lang())
        source = load_transcript(transcript)
        reference = load_glossary(compare) if compare is not None else None
    except (ConfigurationError, FileNotFoundError, ValueError, GlossaryError) as e:
        _fail(str(e))
    context_text = glossary_auto.read_context(context)
    model = model or config.helper_model()
    _confirm_cost(glossary_auto.estimate(source, target_lang, context_text, model), max_cost)
    _log_to_stderr()
    try:
        result = glossary_auto.generate(source, target_lang, context_text, model=model)
    except (RuntimeError, ValueError, ConfigurationError) as e:
        _fail(str(e))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.markdown, encoding="utf-8", newline="\n")
    u = result.usage
    typer.echo(f"Wrote {out}: {len(result.glossary.entries)} terms, {len(result.heard_as)} with corrected "
               f"mis-recognitions, {len(result.dropped)} dropped; {result.cost_usd:.4f} USD "
               f"({u.input_tokens} in, {u.output_tokens} out), {result.wall_seconds:.0f} s")
    for term, forms in result.heard_as.items():
        typer.echo(f"  {term} <- {', '.join(forms)}")
    if result.dropped:
        typer.echo(f"  dropped: {'; '.join(result.dropped)}")
    if reference is not None:
        c = glossary_auto.coverage(result.glossary, reference)
        typer.echo(f"Coverage of {compare.name}: {len(c.covered)}/{c.reference_terms} "
                   f"({_percent(c.share)})")
        if c.wrong_case:
            typer.echo("  right word, wrong case: " + "; ".join(f"{r} as {a}" for r, a in c.wrong_case))
        if c.missing:
            typer.echo(f"  missing: {', '.join(c.missing)}")
        typer.echo(f"  not in the reference ({len(c.noise)}): {', '.join(c.noise)}")


@app.command(name="translate")
def translate_command(
    transcript: Annotated[Path, typer.Argument(help="Corrected transcript JSON.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Translated transcript JSON to write.")],
    lang: Annotated[str | None, typer.Option(help="Target language code. Default: TARGET_LANG.")] = None,
    glossary: Annotated[Path | None, typer.Option(help="Glossary Markdown table of the video.")] = None,
    context: Annotated[Path | None, typer.Option(help="meta.json with title, description, chapters.")] = None,
    model: Annotated[str | None, typer.Option(help="Claude model. Default: TRANSLATION_MODEL.")] = None,
    prompt: Annotated[str, typer.Option(help=f"Prompt version: {' | '.join(translate.VERSIONS)}.")]
    = translate.PROMPT_VERSION,
    max_cost: Annotated[float, typer.Option(help="Stop if the estimate is above this (USD).")]
    = config.COST_CONFIRMATION_USD,
    per_segment: Annotated[bool, typer.Option(
        "--per-segment", help="Translate each segment alone (before 2026-11-10) instead of sentence units.")]
    = False,
) -> None:
    """Claude translation of TRANSCRIPT into --lang, by sentence units (units.py) unless --per-segment."""
    if prompt not in translate.VERSIONS:
        raise typer.BadParameter(f"unknown prompt {prompt!r}; known: {', '.join(translate.VERSIONS)}",
                                 param_hint="--prompt")
    try:
        config.require_api_key()
        target_lang = config.check_lang(lang if lang is not None else config.target_lang())
    except ConfigurationError as e:
        _fail(str(e))
    source, terms, ctx = _stage_inputs(transcript, glossary, context)
    model = model or config.translation_model()
    _confirm_cost(translate.estimate(source, terms, target_lang, ctx, model, version=prompt,
                                     units=not per_segment), max_cost)
    _log_to_stderr()
    try:
        result = translate.translate(source, terms, target_lang, model=model, context=ctx, version=prompt,
                                     units=not per_segment,
                                     progress=lambda n, total: typer.echo(f"  block {n}/{total}", err=True))
    except (RuntimeError, ValueError, ConfigurationError) as e:
        _fail(str(e))
    save_transcript(result, out)
    tr = result.meta["translation"]
    kind = "segments" if per_segment else f"sentence units (from {len(source.segments)} segments)"
    typer.echo(f"Wrote {out}: {len(result.segments)} {kind} in {target_lang}, "
               f"{len(tr['failed_blocks'])} failed blocks; {tr['cost_usd']:.4f} USD")


@app.command(name="srt")
def srt_command(
    transcript: Annotated[Path, typer.Argument(help="Transcript JSON (aligned English or a translation).")],
    out: Annotated[Path, typer.Option("--out", "-o", help="SRT file to write.")],
    glossary: Annotated[Path | None, typer.Option(help="Glossary: no cue break inside a term.")] = None,
) -> None:
    """Subtitles: .srt and a sibling .ass; cues of up to 3 x 48 characters, 1-7 s, split at clause ends."""
    try:
        source = load_transcript(transcript)
        terms = load_glossary(glossary) if glossary is not None else None
        result = subtitles.write_srt(source, out, terms)
    except (FileNotFoundError, ValueError, GlossaryError) as e:
        _fail(str(e))
    typer.echo(f"Wrote {out} and {out.with_suffix('.ass').name}: {result.line()}")


@app.command(name="align")
def align_command(
    transcript: Annotated[Path, typer.Argument(help="Transcript JSON (any engine; times may be estimates).")],
    audio: Annotated[Path, typer.Argument(help="The 16 kHz mono WAV the transcript was made from.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Aligned transcript JSON to write.")],
) -> None:
    """Forced alignment: re-time every word of TRANSCRIPT on AUDIO (needs `uv sync --extra align`)."""
    if not audio.is_file():
        _fail(f"No such file: {audio}")
    _log_to_stderr()
    try:
        source = load_transcript(transcript)
        result = align_module.align(source, audio, align_module.Wav2Vec2Emitter())
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        _fail(str(e))
    save_transcript(result, out)
    a = result.meta["alignment"]
    typer.echo(f"Wrote {out}: {a['words']} words in {len(result.segments)} segments, "
               f"{a['unplaced_words']} unplaced, {a['audio_seconds']:.0f} s of audio "
               f"in {a['wall_seconds']:.0f} s "
               f"(realtime factor {a['realtime_factor']})")


def _write_new(out: Path, text: str, force: bool) -> None:
    """Write `text` to `out`; an existing file (maybe hours of hand edits) needs --force."""
    if out.exists() and not force:
        _fail(f"{out} exists; it may hold hand edits. Pass --force to overwrite it.")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")


@app.command()
def draft(
    transcript: Annotated[Path, typer.Argument(help="Transcript JSON, e.g. work/T2/raw.<engine>.json.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Draft .txt to write.")],
    force: ForceOption = False,
) -> None:
    """Draft for proofreading: one segment per line as [mm:ss] text, a blank line every minute."""
    try:
        source = load_transcript(transcript)
    except (FileNotFoundError, ValueError) as e:
        _fail(str(e))
    _write_new(out, proofread.draft_text(source), force)
    typer.echo(f"Wrote {out}: {len(source.segments)} lines")


@app.command(name="ref-from-draft")
def ref_from_draft(
    draft_file: Annotated[Path, typer.Argument(metavar="DRAFT", help="Proofread draft .txt.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Reference .txt to write.")],
    force: ForceOption = False,
) -> None:
    """Reference transcript from a proofread draft: prefixes removed, one paragraph per minute block."""
    if not draft_file.is_file():
        _fail(f"No such file: {draft_file}")
    reference = proofread.reference_from_draft(draft_file.read_text(encoding="utf-8-sig"))
    if not reference:
        _fail(f"{draft_file} has no text")
    _write_new(out, reference, force)
    paragraphs = len(reference.strip().split("\n\n"))
    typer.echo(f"Wrote {out}: {len(reference.split())} words, {paragraphs} paragraphs")


@app.command()
def run(
    source: Annotated[str, typer.Argument(help="Video URL or local file.")],
    lang: Annotated[str | None, typer.Option(help="Target language code. Default: TARGET_LANG.")] = None,
    steps: Annotated[str, typer.Option(help=f"Comma-separated, any of: {', '.join(STEPS)}.")] = DEFAULT_STEPS,
    engine: Annotated[str, typer.Option(help=f"Speech recognition: {' | '.join(pipeline.ENGINES)}.")]
    = pipeline.DEFAULT_ENGINE,
    glossary: Annotated[Path | None, typer.Option(
        help="Your glossary (Markdown table); merged with the auto-glossary, your entries win.")] = None,
    context: Annotated[str | None, typer.Option(
        help="Video title, description, chapter titles: text, or a file holding it. "
             "A local file's name is not context.")] = None,
    max_cost: Annotated[float, typer.Option(
        help="Stop before the Claude steps if their estimate is above this (USD).")]
    = config.COST_CONFIRMATION_USD,
    name: Annotated[str | None, typer.Option(
        help="Name of the run: work/<name>/ and <name>.<lang>.srt. Default: the file name.")]
    = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the plan and stop.")] = False,
    gemini_cache_only: Annotated[bool, typer.Option(
        "--gemini-cache-only",
        help="No Gemini call: reuse cached Gemini chunks, transcribe the rest with faster-whisper "
             "(for a day when the free quota is used up).")] = False,
) -> None:
    """
    Run the pipeline on SOURCE; every step writes its file in work/<video-id>/ and skips if up to date.
    Default: AssemblyAI, auto-glossary, AssemblyAI with key terms, correction, translation, subtitles.
    """
    selected = parse_steps(steps)
    try:
        target_lang = config.check_lang(lang if lang is not None else config.target_lang())
    except ConfigurationError as e:
        raise typer.BadParameter(str(e), param_hint="--lang") from None
    if engine not in pipeline.ENGINES:
        raise typer.BadParameter(f"unknown engine {engine!r}; known: {', '.join(pipeline.ENGINES)}",
                                 param_hint="--engine")
    if "dub" in selected:
        raise typer.BadParameter("dub arrives in stage 4", param_hint="--steps")
    if not is_url(source) and not Path(source).is_file():
        _fail(f"No such file: {source}")

    typer.echo(f"Source: {source} ({'URL' if is_url(source) else 'local file'})")
    typer.echo(f"Target language: {target_lang}")
    typer.echo("Steps:")
    for number, step_name in enumerate(selected, start=1):
        detail = STEPS[step_name].format(correction=f"{correct.PROMPT_VERSION}, {config.correction_model()}",
                                    translation=f"{translate.PROMPT_VERSION}, {config.translation_model()}",
                                    helper=config.helper_model(), lang=target_lang, engine=engine)
        typer.echo(f"  {number}. {step_name:<10} {detail}")
    settings = pipeline.RunSettings(source=Path(source), target_lang=target_lang, steps=selected,
                                    engine=engine, glossary=glossary, max_cost_usd=max_cost,
                                    context=glossary_auto.read_context(context), name=name or "",
                                    gemini_cache_only=gemini_cache_only)
    try:
        pipeline.check_keys(settings)  # fail before any work, not after speech recognition
    except ConfigurationError as e:
        _fail(str(e))
    if dry_run:
        return
    if is_url(source):
        _fail("Downloading a URL is not implemented yet; download the video and pass the file.")
    _log_to_stderr()
    try:
        report = pipeline.run(settings, progress=_print_progress)
    except (FileNotFoundError, ValueError, RuntimeError, ConfigurationError, GlossaryError) as e:
        _fail(str(e))
    typer.echo(f"Done: {settings.work_dir}")
    typer.echo("Cost report:")
    for line in pipeline.cost_lines(report):
        typer.echo(line)
    for name, r in report.subtitles.items():
        typer.echo(f"  {name}: {r['cues']} cues, {r['share_above_17_cps'] * 100:.1f}% above 17 cps, "
                   f"{r['share_above_25_cps'] * 100:.1f}% above 25 cps")


FRAGMENT_ID = re.compile(r"^T\d+$")


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


@app.command(name="eval")
def eval_command(
    fragment: Annotated[str, typer.Argument(help="Test-set fragment ID, e.g. T2.")],
    hyp: Annotated[Path, typer.Option(help="Hypothesis: a Transcript JSON or a plain .txt.")],
    raw: Annotated[Path | None, typer.Option(
        help="Raw transcript before correction (JSON or .txt); adds the false-edit rate.")] = None,
    record: Annotated[bool, typer.Option(
        "--record", help="Append one row to results/benchmark.csv.")] = False,
    engine: Annotated[str | None, typer.Option(
        help="Engine or prompt version for the benchmark row (with --record).")] = None,
    stage: Annotated[str | None, typer.Option(
        help="Pipeline stage for the benchmark row, e.g. stt or fix (with --record).")] = None,
    notes: Annotated[str, typer.Option(help="Free text for the notes column (with --record).")] = "",
) -> None:
    """Score HYP against data/test-set/<FRAGMENT>.ref.txt: WER, term accuracy, false-edit rate."""
    if not FRAGMENT_ID.match(fragment):
        raise typer.BadParameter(f"expected an ID such as T2, got {fragment!r}", param_hint="FRAGMENT")
    if record and not (engine and engine.strip() and stage and stage.strip()):
        raise typer.BadParameter("--record needs --engine and --stage", param_hint="--record")
    ref_path = config.TEST_SET_DIR / f"{fragment}.ref.txt"
    if not ref_path.is_file():
        _fail(f"No reference transcript {ref_path}. Build it with talk-autopsy adjudicate {fragment} "
              f"..., decide the spans in data/test-set/{fragment}.adjudication.md, then run "
              f"talk-autopsy resolve {fragment}")
    glossary_path = config.TEST_SET_DIR / f"{fragment}.glossary.md"
    try:
        reference = ref_path.read_text(encoding="utf-8")
        hypothesis = evaluation.read_transcript_text(hyp)
        raw_text = evaluation.read_transcript_text(raw).text if raw is not None else None
        glossary = None
        draft_glossary = False
        if glossary_path.is_file():
            glossary_text = glossary_path.read_text(encoding="utf-8")
            glossary = parse_glossary(glossary_text)
            draft_glossary = evaluation.is_draft_glossary(glossary_text)
        report = evaluation.evaluate(reference, hypothesis.text, glossary, raw_text)
    except (FileNotFoundError, ValueError, GlossaryError) as e:
        _fail(str(e))

    w = report.wer
    typer.echo(f"Fragment {fragment}: {hyp}")
    if hypothesis.meta:
        typer.echo(f"Run: {evaluation.describe_meta(hypothesis.meta)}")
    typer.echo(f"  {'WER':<16} {_percent(w.wer):>8}   (S {w.substitutions}, D {w.deletions}, "
               f"I {w.insertions}; {w.reference_words} reference words)")
    if report.terms is None:
        typer.echo(f"  {'term accuracy':<16} {'skipped':>8}   (no {glossary_path.name})")
    else:
        t = report.terms
        draft = "; glossary is a draft, not yet reviewed" if draft_glossary else ""
        typer.echo(f"  {'term accuracy':<16} {_percent(t.accuracy):>8}   "
                   f"({t.found}/{t.expected} occurrences of {len(t.rows)} terms{draft})")
        for row in t.rows:
            mark = "" if row.found == row.expected else "   <-"
            kind = "id" if row.identifier else ""
            typer.echo(f"      {row.term:<36} {kind:<3} {row.found}/{row.expected}{mark}")
        if t.absent:
            typer.echo(f"      not in the reference (excluded): {', '.join(t.absent)}")
    if report.false_edits is not None:
        f = report.false_edits
        typer.echo(f"  {'false-edit rate':<16} {_percent(f.rate):>8}   "
                   f"({f.false_edits} of {f.raw_hits} correct raw words changed)")

    if record:
        if draft_glossary:
            notes = "; ".join(n for n in (notes, "draft glossary") if n)
        row = evaluation.benchmark_row(report, fragment=fragment, stage=stage.strip(),
                                       engine=engine.strip(), meta=hypothesis.meta, notes=notes)
        try:
            evaluation.append_benchmark_row(config.BENCHMARK_CSV, row)
        except (FileNotFoundError, ValueError) as e:
            _fail(str(e))
        typer.echo(f"Recorded in {config.BENCHMARK_CSV}")


def _fragment(fragment: str) -> str:
    if not FRAGMENT_ID.match(fragment):
        raise typer.BadParameter(f"expected an ID such as T2, got {fragment!r}", param_hint="FRAGMENT")
    return fragment


def _consensus_path(fragment: str) -> Path:
    return config.WORK_DIR / fragment / "consensus.json"


@app.command(name="adjudicate")
def adjudicate_command(
    fragment: Annotated[str, typer.Argument(help="Test-set fragment ID, e.g. T2.")],
    backbone: Annotated[Path, typer.Option(help="Transcript with word timings (faster-whisper).")],
    hyp: Annotated[list[Path], typer.Option(help="Cloud transcript; give it twice (AssemblyAI, Gemini).")],
    audio: Annotated[Path | None, typer.Option(
        help="Audio for the clips. Default: data/test-set/<FRAGMENT>.wav.")] = None,
    force: ForceOption = False,
) -> None:
    """Align three transcripts; write the adjudication file and one audio clip per disagreement."""
    fragment = _fragment(fragment)
    if len(hyp) != 2:
        raise typer.BadParameter(f"give --hyp exactly twice, got {len(hyp)}", param_hint="--hyp")
    audio = audio or config.TEST_SET_DIR / f"{fragment}.wav"
    out = config.TEST_SET_DIR / f"{fragment}.adjudication.md"
    if out.exists() and not force:
        _fail(f"{out} exists; it may hold your choices. Pass --force to overwrite it.")
    try:
        transcripts = [load_transcript(path) for path in (backbone, *hyp)]
        seconds = ingest.probe(audio)
        result = consensus_module.build_consensus(fragment, transcripts[0], transcripts[1:], seconds)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        _fail(str(e))
    clip_dir = config.WORK_DIR / fragment / "clips"
    for old in clip_dir.glob("*.wav"):  # clips of an earlier alignment would no longer match the numbers
        old.unlink()
    try:
        clips = consensus_module.write_clips(result, audio, clip_dir)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        _fail(str(e))
    consensus_file = _consensus_path(fragment)
    consensus_file.parent.mkdir(parents=True, exist_ok=True)
    consensus_file.write_text(consensus_module.consensus_to_json(result), encoding="utf-8", newline="\n")
    shown = f"work/{fragment}/clips"
    text = consensus_module.adjudication_markdown(result, shown, link_prefix="../../")
    _write_new(out, text, force=True)
    c = result.counts()
    typer.echo(f"Wrote {out}: {c['words']} words, {c['unanimous_share'] * 100:.1f}% unanimous, "
               f"{c['majority_spans']} majority and {c['split_spans']} split spans, "
               f"{len(clips)} clips ({c['clip_minutes']} min) in {clip_dir}")


@app.command(name="resolve")
def resolve_command(
    fragment: Annotated[str, typer.Argument(help="Test-set fragment ID, e.g. T2.")],
    out: Annotated[Path | None, typer.Option(
        "--out", "-o", help="Reference to write. Default: data/test-set/<FRAGMENT>.ref.txt.")] = None,
    adjudication: Annotated[Path | None, typer.Option(
        help="Filled adjudication file. Default: data/test-set/<FRAGMENT>.adjudication.md "
             "(the judge's copy: <FRAGMENT>.adjudication.judged.md).")] = None,
    force: ForceOption = False,
) -> None:
    """Compose the reference from the agreed words and the variants chosen in the adjudication file."""
    fragment = _fragment(fragment)
    out = out or config.TEST_SET_DIR / f"{fragment}.ref.txt"
    adjudication = adjudication or config.TEST_SET_DIR / f"{fragment}.adjudication.md"
    consensus_file = _consensus_path(fragment)
    for path in (adjudication, consensus_file):
        if not path.is_file():
            _fail(f"No {path}; run talk-autopsy adjudicate {fragment} first")
    try:
        result = consensus_module.consensus_from_json(consensus_file.read_text(encoding="utf-8"))
        choices = consensus_module.parse_choices(adjudication.read_text(encoding="utf-8-sig"))
        resolution = consensus_module.resolve(result, choices)
    except (ValueError, TypeError, KeyError) as e:
        _fail(str(e))
    _write_new(out, resolution.reference, force)
    protocol_path = out.with_name(out.name.removesuffix(".txt") + ".protocol.json")
    protocol_path.write_text(json.dumps(resolution.protocol, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8", newline="\n")
    t = resolution.protocol["totals"]
    typer.echo(f"Wrote {out}: {t['reference_words']} words")
    typer.echo(f"  from consensus (all engines agree): {t['consensus_words']} words")
    for engine in resolution.protocol["engines"]:
        typer.echo(f"  chosen variant had {engine:<16} {t['words_by_engine'][engine]} words "
                   f"in {t['spans_by_engine'][engine]} spans")
    typer.echo(f"  typed by the owner: {t['typed_words']} words in {t['spans_typed']} spans")
    typer.echo(f"  majority spans kept / changed: {t['majority_kept']} / {t['majority_changed']}")
    typer.echo(f"Wrote {protocol_path}")


def _judged_path(fragment: str) -> Path:
    return config.TEST_SET_DIR / f"{fragment}.adjudication.judged.md"


@app.command(name="judge")
def judge_command(
    fragment: Annotated[str, typer.Argument(help="Test-set fragment ID, e.g. T2.")],
    model: Annotated[str | None, typer.Option(
        help="Gemini model. Default: GEMINI_MODEL (gemini-3.8-flash).")] = None,
    limit: Annotated[int | None, typer.Option(min=1, help="Judge at most N spans in this run.")] = None,
    resume: Annotated[bool, typer.Option("--resume", help="Continue work/<FRAGMENT>/judge.jsonl.")] = False,
    pace: Annotated[float, typer.Option(min=0.0, help="Seconds between request starts.")] = (
        judge.PACE_SECONDS),
) -> None:
    """
    Blind audio judge: Gemini listens to every majority and split span's clip, transcribes it,
    then picks one of the shuffled unlabeled variants or types its own text. Writes
    work/<FRAGMENT>/judge.jsonl and data/test-set/<FRAGMENT>.adjudication.judged.md. Prints counts only.
    """
    import datetime

    fragment = _fragment(fragment)
    adjudication = config.TEST_SET_DIR / f"{fragment}.adjudication.md"
    consensus_file = _consensus_path(fragment)
    for path in (adjudication, consensus_file):
        if not path.is_file():
            _fail(f"No {path}; run talk-autopsy adjudicate {fragment} first")
    model = model or config.gemini_model()
    out = config.WORK_DIR / fragment / "judge.jsonl"
    try:
        result = consensus_module.consensus_from_json(consensus_file.read_text(encoding="utf-8"))
        conventions = judge.read_conventions((config.TEST_SET_DIR / "README.md").read_text(encoding="utf-8"))
        summary = judge.run(fragment, result, config.WORK_DIR / fragment / "clips", conventions, out, model,
                            limit=limit, resume=resume, pace_seconds=pace, progress=typer.echo)
        records = judge.load_records(out)
    except (FileNotFoundError, ValueError, RuntimeError, ConfigurationError) as e:
        _fail(str(e))
    filled = judge.filled_markdown(adjudication.read_text(encoding="utf-8-sig"), records, summary.total,
                                   datetime.date.today().isoformat())
    judged = _judged_path(fragment)
    judged.write_text(filled, encoding="utf-8", newline=chr(10))
    stats = judge.run_stats(records)
    typer.echo(f"Judge {model}: {summary.done} of {summary.total} spans judged "
               f"({summary.judged_now} in this run), {summary.remaining} remaining")
    if records:
        typer.echo(f"  chose the Gemini engine's variant: {stats['gemini_chosen']} "
                   f"({stats['gemini_share']:.1%})")
        typer.echo(f"  none of the options: {stats['none']} ({stats['none_share']:.1%})")
        if stats["consistency_share"] is not None:
            typer.echo(f"  heard contains the chosen text: {stats['consistent']} of "
                       f"{stats['consistency_checked']} ({stats['consistency_share']:.1%})")
        typer.echo(f"  heard written before choice: {stats['heard_first_share']:.1%}")
    typer.echo(f"Wrote {out} and {judged}")
    if summary.failed:
        typer.echo(f"  {len(summary.failed)} spans failed twice and are left for the next run")
    if summary.stopped:
        typer.echo(f"Stopped early: {summary.stopped}")
    if summary.remaining:
        typer.echo(f"Rerun `talk-autopsy judge {fragment} --resume` "
                   "(after the daily reset if the quota ran out).")
        raise typer.Exit(code=2)


@app.command(name="judge-compare")
def judge_compare_command(
    fragment: Annotated[str, typer.Argument(help="Test-set fragment ID with decided spans, e.g. T2.")],
) -> None:
    """
    Agreement of the judge's file with the decided adjudication file, overall and by how each
    span was decided (inferred). Prints span texts: never use it on a sealed fragment.
    """
    fragment = _fragment(fragment)
    try:
        result = consensus_module.consensus_from_json(_consensus_path(fragment).read_text(encoding="utf-8"))
        reference = consensus_module.parse_choices(
            (config.TEST_SET_DIR / f"{fragment}.adjudication.md").read_text(encoding="utf-8-sig"))
        judged = consensus_module.parse_choices(_judged_path(fragment).read_text(encoding="utf-8-sig"))
        glossary_file = config.TEST_SET_DIR / f"{fragment}.glossary.md"
        terms = [e.term for e in load_glossary(glossary_file).entries] if glossary_file.is_file() else []
        report = judge.compare(result, reference, judged, terms)
    except (FileNotFoundError, ValueError, RuntimeError, KeyError) as e:
        _fail(str(e))

    def line(name: str, s: dict) -> str:
        share = "-" if s["share"] is None else f"{s['share']:.1%}"
        return f"  {name:<22} {s['agree']:>3} of {s['spans']:<3} {share:>7}"

    typer.echo(f"{fragment}: judge against the decided spans (normalized text equal)")
    typer.echo(line("overall", report["overall"]))
    for name, s in report["by_class"].items():
        typer.echo(line(f"decided by {name}", s))
    for name, s in report["by_verdict"].items():
        typer.echo(line(f"{name} spans", s))
    typer.echo("Disagreements (span, class, decided | judge):")
    for row in report["rows"]:
        if not row["agree"]:
            typer.echo(f"  {row['span']:04d} {row['class']:<8} {row['reference'] or '(no words)'} | "
                       f"{row['judge'] or '(no words)'}")
    out = config.WORK_DIR / fragment / "judge.compare.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + chr(10), encoding="utf-8",
                   newline=chr(10))
    typer.echo(f"Wrote {out}")


@app.command(name="glossary-ref")
def glossary_ref_command(
    fragment: Annotated[str, typer.Argument(help="Test-set fragment ID with a reference, e.g. T6.")],
    model: Annotated[str | None, typer.Option(
        help="Gemini model. Default: GEMINI_MODEL (gemini-3.8-flash).")] = None,
    force: ForceOption = False,
) -> None:
    """
    Reference glossary drafted by Gemini (not Claude) from data/test-set/<FRAGMENT>.ref.txt:
    terms that do not occur in the reference are dropped. Writes <FRAGMENT>.glossary.md. Counts only.
    """
    import datetime

    from talk_autopsy import ref_glossary

    fragment = _fragment(fragment)
    reference = config.TEST_SET_DIR / f"{fragment}.ref.txt"
    out = config.TEST_SET_DIR / f"{fragment}.glossary.md"
    if not reference.is_file():
        _fail(f"No {reference}; resolve {fragment} first")
    if out.exists() and not force:
        _fail(f"{out} exists. Pass --force to overwrite it.")
    try:
        extraction = ref_glossary.extract(reference.read_text(encoding="utf-8"),
                                          model or config.gemini_model())
    except (ValueError, RuntimeError, ConfigurationError) as e:
        _fail(str(e))
    out.write_text(ref_glossary.glossary_file(fragment, extraction, datetime.date.today().isoformat()),
                   encoding="utf-8", newline=chr(10))
    kept = len(extraction.glossary.entries)
    typer.echo(f"Wrote {out}: {kept} terms ({extraction.proposed} proposed by {extraction.model}, "
               f"{extraction.dropped_absent} not in the reference, {extraction.dropped_over_limit} over "
               f"the limit of {ref_glossary.MAX_TERMS})")
    if kept < ref_glossary.MIN_TERMS:
        typer.echo(f"  fewer than {ref_glossary.MIN_TERMS} terms")


def main() -> None:
    """
    The entry point of `talk-autopsy` and `python -m talk_autopsy`: load .env, then
    run the CLI. Tests invoke `app` directly, so they never read a local .env.
    """
    config.load_environment()
    app()
