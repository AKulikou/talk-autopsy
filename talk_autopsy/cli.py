"""
Command line: `cut`, `transcribe` (faster-whisper or a cloud engine), `align`,
`glossary-auto`, `fix`, `translate`, `srt`, `adjudicate`, `resolve`, `judge`,
`judge-refs`, `judge-compare`, `glossary-ref`, `eval`, `doctor`, `run` (the
pipeline, pipeline.py) and `web` (the local web page, talk_autopsy/web/).

Only argument parsing and printing live here; the stages are plain functions in
the package, so a web UI can call them the same way later.
"""

import json
import logging
import re
import sys
from collections import Counter
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
    metrics,
    outputs,
    pipeline,
    subtitles,
    translate,
    youtube,
)
from talk_autopsy.config import ConfigurationError
from talk_autopsy.glossary import (
    GlossaryError,
    combine,
    domain_paths,
    load_glossary,
    parse_glossary,
    to_markdown,
)
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
    "dub": "dubbed audio (stage 4, not implemented)",
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
    """Draft the glossary of a video from its first transcript (glossary-auto-v2, helper model)."""
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
    if prompt == translate.V3:  # term classes first (helper model); the classified glossary next to OUT
        from talk_autopsy import term_policy

        _confirm_cost(term_policy.estimate(terms, None, target_lang), max_cost)
        try:
            classified = term_policy.classify(terms, target_lang)
        except (RuntimeError, ValueError, ConfigurationError) as e:
            _fail(str(e))
        terms = classified.glossary
        glossary_out = out.with_name(out.stem + ".glossary-v3.md")
        glossary_out.parent.mkdir(parents=True, exist_ok=True)
        glossary_out.write_text(to_markdown(terms, sorted({target_lang, *terms.languages()})),
                                encoding="utf-8",
                                newline=chr(10))
        typer.echo(f"Term classes: {classified.classified} classified by {classified.model}, "
                   f"{classified.kept} kept; {classified.cost_usd:.4f} USD -> {glossary_out}")
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
               f"{len(tr['failed_blocks'])} failed blocks; {tr['cost_usd']:.4f} USD"
               + (f"; {len(tr['marker_warnings'])} marker warnings" if "marker_warnings" in tr else ""))


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


def _user_glossary_paths(glossary: Path | None, domain: str | None) -> list[Path]:
    """--glossary first (it wins), then the domain glossary, then its local additions; all parsed now."""
    paths = [glossary] if glossary else []
    if domain:
        paths += domain_paths(domain, config.GLOSSARIES_DIR, config.LOCAL_GLOSSARIES_DIR)
    for path in paths:
        if not Path(path).is_file():
            raise GlossaryError(f"no glossary file {path}")
        load_glossary(path)
    return paths


def _run_glossary(paths: list[Path], work_dir: Path) -> Path | None:
    """The user glossary of a run: one file as given, several combined into work/<id>/user-glossary.md."""
    if not paths:
        return None
    if len(paths) == 1:
        return paths[0]
    combined = combine([load_glossary(p) for p in paths])
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / "user-glossary.md"
    sources = ", ".join(Path(p).name for p in paths)
    out.write_text(f"<!-- user glossary of this run: {sources}, earlier files win -->" + chr(10) * 2
                   + to_markdown(combined), encoding="utf-8", newline=chr(10))
    return out


def _print_report(report) -> None:
    typer.echo("Cost report:")
    for line in pipeline.cost_lines(report):
        typer.echo(line)
    for name, r in report.subtitles.items():
        typer.echo(f"  {name}: {r['cues']} cues, {r['share_above_17_cps'] * 100:.1f}% above 17 cps, "
                   f"{r['share_above_25_cps'] * 100:.1f}% above 25 cps")


def _write_outputs(settings, formats: list[str], dest: Path, stem: str, video: Path,
                   duration: float | None) -> list:
    if "mp4-burned" in formats and duration:
        typer.echo(f"Burn-in: about {outputs.burn_seconds(duration) / 60:.0f} min of encoding "
                   f"({outputs.BURN_SPEED:.2f}x real time measured on the owner's laptop; a slower machine "
                   "may take 0.5-1x)")
    lang = settings.target_lang
    translated_path = settings.work_dir / f"{lang}.json"
    translated = (load_transcript(translated_path) if "bilingual" in formats and translated_path.is_file()
                  else None)
    glossary_file = settings.work_dir / "glossary.md"
    run_glossary = load_glossary(glossary_file) if glossary_file.is_file() else None
    written = outputs.write_outputs(settings.work_dir, settings.video_id, lang, dest, stem, formats,
                                    video=video, glossary=run_glossary, translated=translated,
                                    progress=typer.echo)
    typer.echo(f"Outputs in {dest}:")
    for w in written:
        typer.echo(f"  {w.format:<11} {w.path.name}")
    return written


def _local_estimate(source: Path) -> str:
    """The per-hour estimate for a local file (as for a link), or why there is none."""
    try:
        return youtube.estimate(ingest.probe(source)).line()
    except RuntimeError as e:  # includes ffprobe missing
        return f"none ({e})"


@app.command()
def run(
    source: Annotated[str, typer.Argument(help="YouTube video or playlist URL, or a local file.")],
    lang: Annotated[str | None, typer.Option(help="Target language code. Default: TARGET_LANG.")] = None,
    steps: Annotated[str, typer.Option(help=f"Comma-separated, any of: {', '.join(STEPS)}.")] = DEFAULT_STEPS,
    engine: Annotated[str, typer.Option(help=f"Speech recognition: {' | '.join(pipeline.ENGINES)}.")]
    = pipeline.DEFAULT_ENGINE,
    glossary: Annotated[Path | None, typer.Option(
        help="Your glossary (Markdown table); merged with the auto-glossary, your entries win.")] = None,
    domain: Annotated[str | None, typer.Option(
        help="Domain glossary glossaries/<DOMAIN>.md (+ glossaries/local/<DOMAIN>.md), e.g. web3; "
             "merged after --glossary.")] = None,
    context: Annotated[str | None, typer.Option(
        help="Video title, description, chapter titles: text, or a file holding it. "
             "A local file's name is not context; for a YouTube link it is taken from the video.")] = None,
    output: Annotated[str, typer.Option(
        help=f"Comma-separated outputs: {', '.join(outputs.FORMATS)}, or all. mp4-burned is for TVs.")]
    = outputs.DEFAULT,
    translation: Annotated[str | None, typer.Option(
        help="Translation prompt: v2 (default: translation-v2.1) or v3 (term classes keep / both / "
             "translate / ui). Default: TRANSLATION_VERSION.")] = None,
    max_cost: Annotated[float, typer.Option(
        help="Stop before the Claude steps of a video if their estimate is above this (USD).")]
    = config.COST_CONFIRMATION_USD,
    max_videos: Annotated[int | None, typer.Option(min=1, help="Playlist: at most N videos.")] = None,
    max_total_cost: Annotated[float, typer.Option(
        help="Playlist: stop before a video that would take the estimated total (Claude + speech "
             "recognition) past this (USD).")] = config.PLAYLIST_MAX_COST_USD,
    name: Annotated[str | None, typer.Option(
        help="Name of the run: work/<name>/ and <name>.<lang>.srt. Default: the file name.")]
    = None,
    dry_run: Annotated[bool, typer.Option(
        "--dry-run", help="Print the plan (and a link's metadata) and stop.")] = False,
    gemini_cache_only: Annotated[bool, typer.Option(
        "--gemini-cache-only",
        help="No Gemini call: reuse cached Gemini chunks, transcribe the rest with faster-whisper "
             "(for a day when the free quota is used up).")] = False,
) -> None:
    """
    Run the pipeline on SOURCE; every step writes its file in work/<video-id>/ and skips if up to date.
    Default: AssemblyAI, auto-glossary, AssemblyAI with key terms, correction, translation, subtitles.
    A YouTube link is downloaded first (up to 1080p) into OUTPUT_DIR/<date> <title>/, where the outputs
    go; a local file's outputs go to OUTPUT_DIR/<file name>/.
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
    try:
        formats = outputs.parse_outputs(output)
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--output") from None
    try:
        translation_version = config.translation_version(translation)
    except ConfigurationError as e:
        raise typer.BadParameter(str(e), param_hint="--translation") from None
    if not is_url(source) and not Path(source).is_file():
        _fail(f"No such file: {source}")
    try:
        glossary_paths = _user_glossary_paths(glossary, domain)
    except GlossaryError as e:
        _fail(str(e))

    typer.echo(f"Source: {source} ({'URL' if is_url(source) else 'local file'})")
    typer.echo(f"Target language: {target_lang}")
    typer.echo("Steps:")
    for number, step_name in enumerate(selected, start=1):
        detail = STEPS[step_name].format(correction=f"{correct.PROMPT_VERSION}, {config.correction_model()}",
                                    translation=f"{translation_version}, {config.translation_model()}",
                                    helper=config.helper_model(), lang=target_lang, engine=engine)
        typer.echo(f"  {number}. {step_name:<10} {detail}")
    typer.echo(f"Outputs: {', '.join(formats)}")
    if glossary_paths:
        typer.echo(f"User glossary: {', '.join(str(p) for p in glossary_paths)} (earlier files win)")
    extra_context = glossary_auto.read_context(context)
    if not is_url(source):  # a link's estimate is printed with its metadata
        typer.echo(f"Estimate: {_local_estimate(Path(source))}")

    def settings_for(video_source: Path, run_name: str, video_context: str):
        settings = pipeline.RunSettings(source=video_source, target_lang=target_lang, steps=selected,
                                        engine=engine, max_cost_usd=max_cost, context=video_context,
                                        name=run_name, gemini_cache_only=gemini_cache_only,
                                        translation_version=translation_version)
        if not dry_run:
            settings.glossary = _run_glossary(glossary_paths, settings.work_dir)
        return settings

    local = None if is_url(source) else settings_for(Path(source), name or "", extra_context)
    try:  # fail before any work, not after speech recognition
        pipeline.check_keys(local or pipeline.RunSettings(source=Path(source), target_lang=target_lang,
                                                          steps=selected, engine=engine))
    except ConfigurationError as e:
        _fail(str(e))

    if local is None:
        _run_youtube(source, settings_for, extra_context, formats, max_cost, max_videos, max_total_cost,
                     dry_run)
        return
    if dry_run:
        return
    _log_to_stderr()
    try:
        report = pipeline.run(local, progress=_print_progress)
        typer.echo(f"Done: {local.work_dir}")
        _print_report(report)
        stem = youtube.safe_name(Path(source).stem)
        _write_outputs(local, formats, config.output_dir() / stem, stem, Path(source),
                       ingest.probe(Path(source)) if "mp4-burned" in formats else None)
    except (FileNotFoundError, ValueError, RuntimeError, ConfigurationError, GlossaryError) as e:
        _fail(str(e))


def _run_youtube(url: str, settings_for, extra_context: str, formats: list[str], max_cost: float,
                 max_videos: int | None, max_total_cost: float, dry_run: bool) -> None:
    """A YouTube video or playlist: tools check, metadata and estimate, download, pipeline, outputs."""
    try:
        runtimes = youtube.require_youtube_tools()
        info = youtube.fetch(url, runtimes)
    except youtube.YouTubeError as e:
        _fail(str(e))
    if isinstance(info, youtube.PlaylistInfo):
        parent = config.output_dir() / youtube.safe_name(info.title)
        plan = youtube.plan_playlist(info, parent, max_videos, max_total_cost, max_cost)
        typer.echo(f"Playlist: {info.title} ({len(info.entries)} videos) -> {parent}")
        for video in info.entries[:max_videos] if max_videos else info.entries:
            mark = ("done, skipped" if video in plan.skipped_done else
                    "over the cap, not run" if video in plan.over_cap else "to run")
            typer.echo(f"  {video.title[:60]:<60} {youtube.estimate(video.duration).line()} [{mark}]")
        typer.echo(f"Total estimate for the {len(plan.todo)} videos to run: "
                   f"about {plan.estimate_usd:.2f} USD "
                   f"(cap {max_total_cost:.2f} USD)")
        if plan.over_cap:
            typer.echo(f"Stopping before {len(plan.over_cap)} videos: the next would cross the cap "
                       "(raise --max-total-cost or --max-cost) or has no duration to estimate.")
        if dry_run:
            return
        for number, video in enumerate(plan.todo, start=1):
            typer.echo(f"--- video {number} of {len(plan.todo)}: {video.title}")
            _youtube_video(video, parent, runtimes, settings_for, extra_context, formats, max_cost, dry_run)
        return
    _youtube_video(info, config.output_dir(), runtimes, settings_for, extra_context, formats, max_cost,
                   dry_run)


def _youtube_video(video, parent: Path, runtimes: list[str], settings_for, extra_context: str,
                   formats: list[str], max_cost: float, dry_run: bool) -> None:
    try:
        if not video.description and not video.upload_date:  # a playlist's flat entry: full metadata
            video = youtube.fetch_video(video.url, runtimes)
    except youtube.YouTubeError as e:
        _fail(str(e))
    folder = parent / youtube.video_folder_name(video)
    e = youtube.estimate(video.duration)
    typer.echo(f"Video: {video.title}" + (f" ({video.channel})" if video.channel else ""))
    typer.echo(f"  {e.line()}")
    typer.echo(f"  author's captions: {', '.join(video.author_captions) or 'none'}; "
               f"chapters: {len(video.chapters)}")
    typer.echo(f"  folder: {folder}")
    if e.claude_usd > max_cost:
        _fail(f"the Claude estimate {e.claude_usd:.2f} USD is above --max-cost {max_cost:.2f} USD")
    if dry_run:
        return
    try:
        download = youtube.download(video, folder, runtimes)
    except youtube.YouTubeError as err:
        _fail(str(err))
    typer.echo(f"Downloaded {download.video.name}"
               + (f" + {len(download.captions)} caption file(s)" if download.captions else ""))
    context = video.context() + (chr(10) * 2 + extra_context if extra_context else "")
    settings = settings_for(download.video, f"yt-{youtube.safe_name(video.id, 40)}", context)
    _log_to_stderr()
    try:
        report = pipeline.run(settings, progress=_print_progress)
        _print_report(report)
        written = _write_outputs(settings, formats, folder, youtube.video_file_stem(video), download.video,
                                 video.duration)
    except (FileNotFoundError, ValueError, RuntimeError, ConfigurationError, GlossaryError) as err:
        _fail(str(err))
    youtube.mark_done(folder, [w.path.name for w in written],
                      {"claude_usd": report.cost_usd, "stt_usd": report.stt_cost_usd})


@app.command()
def doctor() -> None:
    """Check what the tool needs here: ffmpeg, a JavaScript runtime for YouTube, yt-dlp, API keys (names)."""
    checks = youtube.doctor()
    failed = False
    for c in checks:
        status = "ok" if c.ok else ("MISSING" if c.required else "warning")
        typer.echo(f"  {status:<8} {c.name}: {c.detail}")
        if not c.ok:
            typer.echo(f"           fix: {c.fix}")
            failed = failed or c.required
    if failed:
        raise typer.Exit(code=1)


@app.command()
def web(
    port: Annotated[int | None, typer.Option(help="Port on 127.0.0.1 (default 8765, or the next free one).")]
    = None,
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Do not open the browser.")] = False,
    replay: Annotated[Path | None, typer.Option(
        hidden=True, help="Play a recorded event stream (.jsonl) instead of running anything (docs/web.md).")]
    = None,
    speed: Annotated[float, typer.Option(hidden=True, help="Replay speed: 1 natural, 10 ten times faster, "
                                                           "0 all at once.")] = 1.0,
) -> None:
    """Start the local web page (this computer only) and open it in the browser; Ctrl+C stops it."""
    from talk_autopsy.web.replay import RecordingError
    from talk_autopsy.web.server import serve
    try:
        serve(port, open_browser=not no_browser, echo=typer.echo, replay=replay, speed=speed,
              reuse=replay is None)
    except RecordingError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(2) from None


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
    if fragment in config.SEALED_FRAGMENTS:
        _eval_sealed(fragment, ref_path, glossary_path, hyp, raw, record, engine, stage, notes)
        return
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
        typer.echo(f"  {'term accuracy':<16} {_percent(t.accuracy):>8}   (v2, in place: "
                   f"{t.found}/{t.expected} occurrences of {len(t.rows)} terms{draft})")
        typer.echo(f"  {'  macro':<16} {_percent(t.macro):>8}   (each term weighs the same)")
        typer.echo(f"  {'  without top':<16} {_percent(t.micro_without_top):>8}   (without {t.top_term})")
        typer.echo(f"  {'  precision':<16} {_percent(t.precision):>8}   ({t.false_positives} of "
                   f"{t.hypothesis_occurrences} term occurrences in the hypothesis not in place)")
        if report.terms_v1 is not None:
            typer.echo(f"  {'  v1 (old)':<16} {_percent(report.terms_v1.accuracy):>8}   "
                       "(position-free, superseded)")
        for row in t.rows:
            mark = "" if row.found == row.expected else "   <-"
            extra = f"   +{row.false_positives} elsewhere" if row.false_positives else ""
            kind = "id" if row.identifier else ""
            typer.echo(f"      {row.term:<36} {kind:<3} {row.found}/{row.expected}{mark}{extra}")
        if t.absent:
            typer.echo(f"      not in the reference (excluded): {', '.join(t.absent)}")
    if report.false_edits is not None:
        f = report.false_edits
        typer.echo(f"  {'false-edit rate':<16} {_percent(f.rate):>8}   ({f.false_edits} of {f.edits} word "
                   f"edits move the text away from the reference; {f.helpful_edits} move it closer)")

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


def _eval_sealed(fragment: str, ref_path: Path, glossary_path: Path, hyp: Path, raw: Path | None,
                 record: bool, engine: str | None, stage: str | None, notes: str) -> None:
    """
    The sealed hold-out (T6): WER and term accuracy against both references (J: the judge's text,
    M: the engines' majority where one exists), the bounds from the uncertain spans and the gate
    verdict (metrics.bounded_term_accuracy). Counts only: no term, no transcript text.
    """
    from talk_autopsy import ref_bounds

    majority_path, uncertain_path = _majority_ref_path(fragment), _uncertain_path(fragment)
    for path, how in ((majority_path, f"talk-autopsy judge-refs {fragment}"),
                      (uncertain_path, f"talk-autopsy judge-refs {fragment}"),
                      (glossary_path, f"talk-autopsy glossary-ref {fragment}")):
        if not path.is_file():
            _fail(f"No {path}; the sealed fragment is scored with both references and the bounds ({how})")
    try:
        reference_j = ref_path.read_text(encoding="utf-8")
        reference_m = majority_path.read_text(encoding="utf-8")
        ranges = ref_bounds.load_uncertain(uncertain_path, reference_j, reference_m)
        glossary = parse_glossary(glossary_path.read_text(encoding="utf-8"))
        hypothesis = evaluation.read_transcript_text(hyp)
        raw_text = evaluation.read_transcript_text(raw).text if raw is not None else None
        report = evaluation.evaluate(reference_j, hypothesis.text, glossary, raw_text)
        bounds = metrics.bounded_term_accuracy(reference_j, reference_m, hypothesis.text,
                                               [e.term for e in glossary.entries], ranges)
        wer_m = metrics.word_error_rate(reference_m, hypothesis.text)
    except (FileNotFoundError, ValueError, KeyError, GlossaryError) as e:
        _fail(str(e))

    typer.echo(f"Fragment {fragment} (sealed: counts only): {hyp}")
    if hypothesis.meta:
        typer.echo(f"Run: {evaluation.describe_meta(hypothesis.meta)}")
    for name, w in (("WER against J", report.wer), ("WER against M", wer_m)):
        typer.echo(f"  {name:<26} {_percent(w.wer):>8}   (S {w.substitutions}, D {w.deletions}, "
                   f"I {w.insertions}; {w.reference_words} reference words)")
    for name, t, interval in (("term accuracy against J", bounds.against_j, bounds.interval_j),
                              ("term accuracy against M", bounds.against_m, bounds.interval_m)):
        typer.echo(f"  {name:<26} {_percent(t.accuracy):>8}   ({t.found}/{t.expected} occurrences of "
                   f"{len(t.rows)} terms, in place; Wilson 95% {_interval(interval)}; macro "
                   f"{_percent(t.macro)}; without the top term {_percent(t.micro_without_top)}; "
                   f"precision {_percent(t.precision)})")
    typer.echo(f"  {'term accuracy, lower bound':<26} {_percent(bounds.lower):>8}   "
               f"({bounds.uncertain_occurrences} of {bounds.occurrences} occurrences inside uncertain "
               f"spans counted as misses; Wilson 95% {_interval(bounds.interval_lower)})")
    typer.echo(f"  {'term accuracy, upper bound':<26} {_percent(bounds.upper):>8}   (counted as hits; "
               f"Wilson 95% {_interval(bounds.interval_upper)})")
    typer.echo(f"  gate: term accuracy >= {bounds.threshold:.0%}: {bounds.verdict.upper()}   "
               "(docs/experiments.md, T6 protocol)")
    t2 = _t2_reference_values()
    if t2:
        signal = metrics.overfitting_signal(bounds.against_j.accuracy or 0.0, t2)
        typer.echo(f"  overfitting signal: {'YES' if signal else 'no'}   "
                   f"(J {_percent(bounds.against_j.accuracy)} against the T2 mean "
                   f"{_percent(sum(t2) / len(t2))} of {len(t2)} pipeline-v3 repeats; "
                   f"signal if more than {metrics.OVERFITTING_MARGIN:.0%} below)")
    else:
        typer.echo("  overfitting signal: not computed (no current pipeline-v3 T2 rows in "
                   "results/benchmark.csv)")
    if report.false_edits is not None:
        f = report.false_edits
        typer.echo(f"  {'false-edit rate (J)':<26} {_percent(f.rate):>8}   "
                   f"({f.false_edits} of {f.edits} word edits move the text away from J)")
    if record:
        m_terms = bounds.against_m.accuracy
        summary = (f"reference J; M: WER {wer_m.wer:.4f}, terms v2 "
                   f"{'n/a' if m_terms is None else f'{m_terms:.4f}'}; term bounds v2 "
                   f"[{bounds.lower:.4f}, {bounds.upper:.4f}], Wilson lower limit of the lower bound "
                   f"{bounds.interval_lower[0]:.4f}; gate {bounds.verdict}")
        notes = "; ".join(n for n in (notes, summary) if n)
        row = evaluation.benchmark_row(report, fragment=fragment, stage=stage.strip(), engine=engine.strip(),
                                       meta=hypothesis.meta, notes=notes)
        try:
            evaluation.append_benchmark_row(config.BENCHMARK_CSV, row)
        except (FileNotFoundError, ValueError) as e:
            _fail(str(e))
        typer.echo(f"Recorded in {config.BENCHMARK_CSV}")


def _interval(interval: tuple[float, float]) -> str:
    return f"[{interval[0] * 100:.1f}%, {interval[1] * 100:.1f}%]"


PIPELINE_V3 = "pipeline-v3"


def _t2_reference_values() -> list[float]:
    """Term accuracy v2 (micro) of the current pipeline-v3 rows on T2: the overfitting comparison point."""
    import csv

    if not config.BENCHMARK_CSV.is_file():
        return []
    with config.BENCHMARK_CSV.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [float(r["term_accuracy_v2"]) for r in rows
            if r.get("fragment") == "T2" and r.get("engine_or_prompt") == PIPELINE_V3
            and r.get("status") == "current" and r.get("term_accuracy_v2")]


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
    normalize_adjudication: Annotated[bool, typer.Option(
        "--normalize-adjudication",
        help="Pass every span's chosen text through the adjudication normaliser (repetitions written "
             "once, thousands separators, key combinations); for a reference built from a judged file.",
    )] = False,
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
        resolution = consensus_module.resolve(
            result, choices, postprocess=judge.normalize_for_adjudication if normalize_adjudication else None)
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
        help="Gemini model. Default: JUDGE_MODEL (gemini-4-carbon); never the STT engine's model.")] = None,
    limit: Annotated[int | None, typer.Option(min=1, help="Judge at most N spans in this run.")] = None,
    resume: Annotated[bool, typer.Option("--resume", help="Continue work/<FRAGMENT>/judge.jsonl.")] = False,
    pace: Annotated[float, typer.Option(min=0.0, help="Seconds between request starts.")] = (
        judge.PACE_SECONDS),
    budget: Annotated[float, typer.Option(
        min=0.0, help="Session budget in USD (the run's own counter).")] = config.JUDGE_BUDGET_USD,
    spent_before: Annotated[float, typer.Option(
        min=0.0, help="USD the session spent before this run.")] = 0.0,
    plan_after: Annotated[int, typer.Option(
        min=0, help="Spans of later work in the same budget, for the projection (e.g. T6 after T2).")] = 0,
) -> None:
    """
    Blind audio judge: Gemini listens to every majority and split span's clip, transcribes it,
    then picks one of the shuffled unlabeled variants or types its own text. Writes
    work/<FRAGMENT>/judge.jsonl and data/test-set/<FRAGMENT>.adjudication.judged.md. Prints counts
    and cost only.
    """
    import datetime

    fragment = _fragment(fragment)
    adjudication = config.TEST_SET_DIR / f"{fragment}.adjudication.md"
    consensus_file = _consensus_path(fragment)
    for path in (adjudication, consensus_file):
        if not path.is_file():
            _fail(f"No {path}; run talk-autopsy adjudicate {fragment} first")
    model = model or config.judge_model()
    if model in config.gemini_models():
        _fail(f"{model} is the Gemini STT engine's model; the judge must be a different model")
    thinking = config.judge_thinking_level()
    typer.echo(f"Judge {model}, thinking level {thinking}")
    out = config.WORK_DIR / fragment / "judge.jsonl"
    try:
        result = consensus_module.consensus_from_json(consensus_file.read_text(encoding="utf-8"))
        conventions = judge.read_conventions((config.TEST_SET_DIR / "README.md").read_text(encoding="utf-8"))
        summary = judge.run(fragment, result, config.WORK_DIR / fragment / "clips", conventions, out, model,
                            limit=limit, resume=resume, pace_seconds=pace, progress=typer.echo,
                            thinking_level=thinking, budget_usd=budget, spent_before_usd=spent_before,
                            plan_after=plan_after)
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
        typer.echo(f"  cost of the recorded spans: ${stats['cost_usd']:.4f}")
    session = spent_before + summary.spent_usd
    typer.echo(f"Spent in this run: ${summary.spent_usd:.4f} (session ${session:.4f} of ${budget:.2f})")
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
    normalize_adjudication: Annotated[bool, typer.Option(
        "--normalize-adjudication",
        help="Compare after the adjudication normaliser on both sides (post-hoc rescoring, 2026-11-10); "
             "writes judge.compare.normalized.json.")] = False,
) -> None:
    """
    Agreement of the judge's file with the decided adjudication file, overall and by how each
    span was decided (inferred), and the WER between the decided reference and one resolved from
    the judged file. Prints span texts: never use it on a sealed fragment.
    """
    fragment = _fragment(fragment)
    if fragment in config.SEALED_FRAGMENTS:
        _fail(f"{fragment} is sealed; judge-compare prints span texts")
    normalize = judge.normalize_for_adjudication if normalize_adjudication else None
    try:
        result = consensus_module.consensus_from_json(_consensus_path(fragment).read_text(encoding="utf-8"))
        reference = consensus_module.parse_choices(
            (config.TEST_SET_DIR / f"{fragment}.adjudication.md").read_text(encoding="utf-8-sig"))
        judged = consensus_module.parse_choices(_judged_path(fragment).read_text(encoding="utf-8-sig"))
        glossary_file = config.TEST_SET_DIR / f"{fragment}.glossary.md"
        terms = [e.term for e in load_glossary(glossary_file).entries] if glossary_file.is_file() else []
        ear_file = config.WORK_DIR / fragment / "owner-by-ear.json"  # the owner's list, if recorded
        by_ear = ({int(s["span"]) for s in json.loads(ear_file.read_text(encoding="utf-8"))["spans"]}
                  if ear_file.is_file() else set())
        report = judge.compare(result, reference, judged, terms, by_ear, normalize=normalize)
        decided_ref = consensus_module.resolve(result, reference).reference
        judged_ref = consensus_module.resolve(result, judged, postprocess=normalize).reference
        records = {r["span"]: r for r in judge.load_records(config.WORK_DIR / fragment / "judge.jsonl")}
    except (FileNotFoundError, ValueError, RuntimeError, KeyError) as e:
        _fail(str(e))
    if normalize:  # the decided reference passes through the same normaliser: both sides alike
        decided_ref = normalize(decided_ref)
        judged_ref = normalize(judged_ref)
    wer = metrics.word_error_rate(decided_ref, judged_ref)
    report["references_wer"] = {"wer": round(wer.wer, 4), "substitutions": wer.substitutions,
                                "deletions": wer.deletions, "insertions": wer.insertions,
                                "reference_words": wer.reference_words}

    def line(name: str, s: dict) -> str:
        share = "-" if s["share"] is None else f"{s['share']:.1%}"
        return f"  {name:<22} {s['agree']:>3} of {s['spans']:<3} {share:>7}"

    typer.echo(f"{fragment}: judge against the decided spans (normalized text equal)")
    typer.echo(line("overall", report["overall"]))
    for name, s in report["by_class"].items():
        typer.echo(line(f"decided by {name}", s))
    for name, s in report["by_verdict"].items():
        typer.echo(line(f"{name} spans", s))
    typer.echo(f"WER between the decided reference and the judged one: {wer.wer:.2%} "
               f"({wer.reference_words} words; {wer.substitutions} S, {wer.deletions} D, {wer.insertions} I)")
    typer.echo("Disagreements (span, class, decided | judge):")
    for row in report["rows"]:
        if not row["agree"]:
            typer.echo(f"  {row['span']:04d} {row['class']:<8} {row['reference'] or '(no words)'} | "
                       f"{row['judge'] or '(no words)'}")
            if normalize:
                record = records.get(row["span"], {})
                typer.echo(f"       [{row['disagreement']}] heard: {record.get('heard', '?')!r}; "
                           f"final_text: {record.get('final_text_raw', '?')!r}")
    if normalize:
        counts = Counter(r["disagreement"] for r in report["rows"] if not r["agree"])
        typer.echo("Remaining disagreements by class: "
                   + ", ".join(f"{c} {counts.get(c, 0)}" for c in ("term", "word", "formatting")))
    name = "judge.compare.normalized.json" if normalize else "judge.compare.json"
    out = config.WORK_DIR / fragment / name
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + chr(10), encoding="utf-8",
                   newline=chr(10))
    typer.echo(f"Wrote {out}")


def _majority_ref_path(fragment: str) -> Path:
    return config.TEST_SET_DIR / f"{fragment}.ref.majority.txt"


def _uncertain_path(fragment: str) -> Path:
    return config.WORK_DIR / fragment / "uncertain_spans.json"


@app.command(name="judge-refs")
def judge_refs_command(
    fragment: Annotated[str, typer.Argument(help="Test-set fragment ID judged by the audio judge, e.g. T6.")],
    force: ForceOption = False,
) -> None:
    """
    Two references from the judge's records: J (the judge's text in every judged span) ->
    <FRAGMENT>.ref.txt, M (the engines' majority where one exists, the judge on splits) ->
    <FRAGMENT>.ref.majority.txt, and work/<FRAGMENT>/uncertain_spans.json (split spans plus majority
    spans where J and M differ). With <FRAGMENT>.glossary.md present, also counts its term occurrences
    in J and inside uncertain spans. Prints counts only.
    """
    import datetime

    from talk_autopsy import ref_bounds

    fragment = _fragment(fragment)
    consensus_file = _consensus_path(fragment)
    records_file = config.WORK_DIR / fragment / "judge.jsonl"
    for path in (consensus_file, records_file):
        if not path.is_file():
            _fail(f"No {path}; run talk-autopsy adjudicate and judge {fragment} first")
    out_j = config.TEST_SET_DIR / f"{fragment}.ref.txt"
    out_m = _majority_ref_path(fragment)
    try:
        result = consensus_module.consensus_from_json(consensus_file.read_text(encoding="utf-8"))
        records = judge.load_records(records_file)
        refs = ref_bounds.build(result, records)
    except (ValueError, KeyError, RuntimeError) as e:
        _fail(str(e))
    for path, text in ((out_j, refs.j.reference), (out_m, refs.m.reference)):
        if path.exists() and not force and path.read_text(encoding="utf-8") != text:
            _fail(f"{path} exists with other text. Pass --force to overwrite it.")
    for path, resolution in ((out_j, refs.j), (out_m, refs.m)):
        path.write_text(resolution.reference, encoding="utf-8", newline=chr(10))
        protocol = path.with_name(path.name.removesuffix(".txt") + ".protocol.json")
        protocol.write_text(json.dumps(resolution.protocol, ensure_ascii=False, indent=2) + chr(10),
                            encoding="utf-8", newline=chr(10))
    model = records[0].get("model", "?")
    uncertain_file = _uncertain_path(fragment)
    uncertain_file.write_text(
        ref_bounds.uncertain_json(fragment, refs, model, out_j.as_posix(), out_m.as_posix(),
                                  datetime.date.today().isoformat()),
        encoding="utf-8", newline=chr(10))
    c = refs.counts
    typer.echo(f"{fragment}: two references from {len(records)} spans judged by {model}")
    typer.echo(f"  spans judged: majority {c['judged']['majority']}, split {c['judged']['split']}")
    typer.echo(f"  majority spans where the judge overrode the majority: {c['majority_overridden']}")
    typer.echo(f"  uncertain spans (split + overridden majority): {c['uncertain_spans']}")
    typer.echo(f"  judged spans whose final_text differs from the chosen option: "
               f"{c['final_text_differs_from_chosen_option']}")
    w = c["wer_j_m"]
    typer.echo(f"  words: J {c['words_j']}, M {c['words_m']}; WER of M against J {w['wer']:.2%} "
               f"({w['substitutions']} S, {w['deletions']} D, {w['insertions']} I)")
    glossary_file = config.TEST_SET_DIR / f"{fragment}.glossary.md"
    if glossary_file.is_file():
        try:
            terms = [e.term for e in load_glossary(glossary_file).entries]
            ranges = [tuple(s["J"]["words"]) for s in refs.uncertain]
            total, inside = ref_bounds.term_occurrences_inside(refs.j.reference, terms, ranges)
        except (ValueError, GlossaryError) as e:
            _fail(str(e))
        typer.echo(f"  glossary: {len(terms)} terms; {total} occurrences in J, "
                   f"{inside} inside uncertain spans")
    typer.echo(f"Wrote {out_j}, {out_m} (+ protocols) and {uncertain_file}")


@app.command(name="glossary-ref")
def glossary_ref_command(
    fragment: Annotated[str, typer.Argument(help="Test-set fragment ID with a reference, e.g. T6.")],
    model: Annotated[str | None, typer.Option(
        help="Gemini model. Default: JUDGE_MODEL (gemini-4-carbon).")] = None,
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
                                          model or config.judge_model(),
                                          thinking_level=config.judge_thinking_level())
    except (ValueError, RuntimeError, ConfigurationError) as e:
        _fail(str(e))
    out.write_text(ref_glossary.glossary_file(fragment, extraction, datetime.date.today().isoformat()),
                   encoding="utf-8", newline=chr(10))
    kept = len(extraction.glossary.entries)
    typer.echo(f"Wrote {out}: {kept} terms ({extraction.proposed} proposed by {extraction.model}, "
               f"{extraction.dropped_absent} not in the reference, {extraction.dropped_over_limit} over "
               f"the limit of {ref_glossary.MAX_TERMS})")
    typer.echo(f"  cost ${extraction.cost_usd:.4f} ({extraction.tokens['prompt']} input, "
               f"{extraction.tokens['output']} output, {extraction.tokens['thinking']} thinking tokens)")
    if kept < ref_glossary.MIN_TERMS:
        typer.echo(f"  fewer than {ref_glossary.MIN_TERMS} terms")


def main() -> None:
    """
    The entry point of `talk-autopsy` and `python -m talk_autopsy`: load .env, then
    run the CLI. Tests invoke `app` directly, so they never read a local .env.
    """
    config.load_environment()
    app()
