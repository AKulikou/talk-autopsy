"""The Claude stages and subtitles one at a time (experiments): `fix`, `glossary-auto`, `translate`, `srt`."""

from pathlib import Path
from typing import Annotated

import typer

from talk_autopsy import config, correct, glossary_auto, subtitles, translate
from talk_autopsy.cli.common import confirm_cost, fail, log_to_stderr, percent, stage_inputs
from talk_autopsy.config import ConfigurationError
from talk_autopsy.glossary import GlossaryError, load_glossary, to_markdown
from talk_autopsy.segments import load_transcript, save_transcript


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
        fail(str(e))
    source, terms, ctx = stage_inputs(transcript, glossary, context)
    model = model or config.correction_model()
    confirm_cost(correct.estimate(source, terms, ctx, model, version=prompt), max_cost)
    log_to_stderr()
    try:
        result = correct.correct(source, terms, ctx, model=model, version=prompt,
                                 progress=lambda n, total: typer.echo(f"  block {n}/{total}", err=True))
    except (RuntimeError, ValueError, ConfigurationError) as e:
        fail(str(e))
    save_transcript(result, out)
    c = result.meta["correction"]
    typer.echo(f"Wrote {out}: {c['segments_changed']} of {len(result.segments)} segments changed, "
               f"{len(c['edits'])} edits, {len(c['failed_blocks'])} failed blocks; {c['cost_usd']:.4f} USD "
               f"({c['usage']['input_tokens']} in, {c['usage']['cache_creation_input_tokens']} cache write, "
               f"{c['usage']['cache_read_input_tokens']} cache read, {c['usage']['output_tokens']} out)")


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
        fail(str(e))
    context_text = glossary_auto.read_context(context)
    model = model or config.helper_model()
    confirm_cost(glossary_auto.estimate(source, target_lang, context_text, model), max_cost)
    log_to_stderr()
    try:
        result = glossary_auto.generate(source, target_lang, context_text, model=model)
    except (RuntimeError, ValueError, ConfigurationError) as e:
        fail(str(e))
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
                   f"({percent(c.share)})")
        if c.wrong_case:
            typer.echo("  right word, wrong case: " + "; ".join(f"{r} as {a}" for r, a in c.wrong_case))
        if c.missing:
            typer.echo(f"  missing: {', '.join(c.missing)}")
        typer.echo(f"  not in the reference ({len(c.noise)}): {', '.join(c.noise)}")


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
        fail(str(e))
    source, terms, ctx = stage_inputs(transcript, glossary, context)
    model = model or config.translation_model()
    if prompt == translate.V3:  # term classes first (helper model); the classified glossary next to OUT
        from talk_autopsy import term_policy

        confirm_cost(term_policy.estimate(terms, None, target_lang), max_cost)
        try:
            classified = term_policy.classify(terms, target_lang)
        except (RuntimeError, ValueError, ConfigurationError) as e:
            fail(str(e))
        terms = classified.glossary
        glossary_out = out.with_name(out.stem + ".glossary-v3.md")
        glossary_out.parent.mkdir(parents=True, exist_ok=True)
        glossary_out.write_text(to_markdown(terms, sorted({target_lang, *terms.languages()})),
                                encoding="utf-8",
                                newline=chr(10))
        typer.echo(f"Term classes: {classified.classified} classified by {classified.model}, "
                   f"{classified.kept} kept; {classified.cost_usd:.4f} USD -> {glossary_out}")
    confirm_cost(translate.estimate(source, terms, target_lang, ctx, model, version=prompt,
                                     units=not per_segment), max_cost)
    log_to_stderr()
    try:
        result = translate.translate(source, terms, target_lang, model=model, context=ctx, version=prompt,
                                     units=not per_segment,
                                     progress=lambda n, total: typer.echo(f"  block {n}/{total}", err=True))
    except (RuntimeError, ValueError, ConfigurationError) as e:
        fail(str(e))
    save_transcript(result, out)
    tr = result.meta["translation"]
    kind = "segments" if per_segment else f"sentence units (from {len(source.segments)} segments)"
    typer.echo(f"Wrote {out}: {len(result.segments)} {kind} in {target_lang}, "
               f"{len(tr['failed_blocks'])} failed blocks; {tr['cost_usd']:.4f} USD"
               + (f"; {len(tr['marker_warnings'])} marker warnings" if "marker_warnings" in tr else ""))


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
        fail(str(e))
    typer.echo(f"Wrote {out} and {out.with_suffix('.ass').name}: {result.line()}")
