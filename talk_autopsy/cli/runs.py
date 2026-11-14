"""`run`: the whole pipeline on a local file, a YouTube video or a playlist."""

from pathlib import Path
from typing import Annotated

import typer

from talk_autopsy import config, correct, glossary_auto, ingest, outputs, pipeline, user_glossary, youtube
from talk_autopsy.cli.common import fail, log_to_stderr, print_progress
from talk_autopsy.config import ConfigurationError
from talk_autopsy.glossary import GlossaryError, load_glossary
from talk_autopsy.segments import load_transcript
from talk_autopsy.web.settings import VIDEO_FORMATS
from talk_autopsy.web.sources import is_audio_only

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


def _local_estimate(source: Path) -> tuple[youtube.Estimate | None, str]:
    """The per-hour estimate for a local file (as for a link) and its line, or None and why there is none."""
    try:
        e = youtube.estimate(ingest.probe(source))
    except RuntimeError as err:  # includes ffprobe missing
        return None, f"none ({err})"
    return e, e.line()


def _check_video_limit(e: youtube.Estimate, max_cost: float) -> None:
    """Stop before a video whose whole estimate is over --max-cost (the same limit as the web page)."""
    if youtube.over_video_limit(e, max_cost):
        fail(f"the estimate {e.total_usd:.2f} USD (Claude {e.claude_usd:.2f} + speech recognition "
             f"{e.stt_usd:.2f}) is above --max-cost {max_cost:.2f} USD; raise it to confirm")


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
        help="Per-video limit: stop before a video whose estimate (Claude + speech recognition) is "
             "above this (USD).")]
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
        fail(f"No such file: {source}")
    pictures = []  # an audio file has no picture for these (the web page drops them too, web/jobs.py)
    if not is_url(source) and is_audio_only(Path(source)):
        pictures = [f for f in formats if f in VIDEO_FORMATS]
        formats = [f for f in formats if f not in VIDEO_FORMATS] or ["srt"]
    try:
        glossary_paths = user_glossary.user_glossary_paths(glossary, domain)
    except GlossaryError as e:
        fail(str(e))

    typer.echo(f"Source: {source} ({'URL' if is_url(source) else 'local file'})")
    typer.echo(f"Target language: {target_lang}")
    typer.echo("Steps:")
    for number, step_name in enumerate(selected, start=1):
        detail = STEPS[step_name].format(correction=f"{correct.PROMPT_VERSION}, {config.correction_model()}",
                                    translation=f"{translation_version}, {config.translation_model()}",
                                    helper=config.helper_model(), lang=target_lang, engine=engine)
        typer.echo(f"  {number}. {step_name:<10} {detail}")
    typer.echo(f"Outputs: {', '.join(formats)}")
    if pictures:
        typer.echo(f"  audio file: no picture for {', '.join(pictures)}; those outputs are skipped")
    if glossary_paths:
        typer.echo(f"User glossary: {', '.join(str(p) for p in glossary_paths)} (earlier files win)")
    extra_context = glossary_auto.read_context(context)
    if not is_url(source):  # a link's estimate is printed with its metadata
        local_estimate, line = _local_estimate(Path(source))
        typer.echo(f"Estimate: {line}")
        if local_estimate is not None:
            _check_video_limit(local_estimate, max_cost)

    def settings_for(video_source: Path, run_name: str, video_context: str):
        settings = pipeline.RunSettings(source=video_source, target_lang=target_lang, steps=selected,
                                        engine=engine, max_cost_usd=max_cost, context=video_context,
                                        name=run_name, gemini_cache_only=gemini_cache_only,
                                        translation_version=translation_version)
        if not dry_run:
            settings.glossary = user_glossary.run_glossary(glossary_paths, settings.work_dir)
        return settings

    local = None if is_url(source) else settings_for(Path(source), name or "", extra_context)
    try:  # fail before any work, not after speech recognition
        pipeline.check_keys(local or pipeline.RunSettings(source=Path(source), target_lang=target_lang,
                                                          steps=selected, engine=engine))
    except ConfigurationError as e:
        fail(str(e))

    if local is None:
        _run_youtube(source, settings_for, extra_context, formats, max_cost, max_videos, max_total_cost,
                     dry_run)
        return
    if dry_run:
        return
    log_to_stderr()
    try:
        report = pipeline.run(local, progress=print_progress)
        typer.echo(f"Done: {local.work_dir}")
        _print_report(report)
        stem = youtube.local_result_name(Path(source).stem, config.output_dir())
        _write_outputs(local, formats, config.output_dir() / stem, stem, Path(source),
                       ingest.probe(Path(source)) if "mp4-burned" in formats else None)
    except (FileNotFoundError, ValueError, RuntimeError, ConfigurationError, GlossaryError) as e:
        fail(str(e))


def _run_youtube(url: str, settings_for, extra_context: str, formats: list[str], max_cost: float,
                 max_videos: int | None, max_total_cost: float, dry_run: bool) -> None:
    """A YouTube video or playlist: tools check, metadata and estimate, download, pipeline, outputs."""
    try:
        runtimes = youtube.require_youtube_tools()
        info = youtube.fetch(url, runtimes)
    except youtube.YouTubeError as e:
        fail(str(e))
    if isinstance(info, youtube.PlaylistInfo):
        parent = config.output_dir() / youtube.playlist_folder_name(info.title, config.output_dir())
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
        fail(str(e))
    folder = parent / youtube.video_folder_name(video, parent)
    e = youtube.estimate(video.duration)
    typer.echo(f"Video: {video.title}" + (f" ({video.channel})" if video.channel else ""))
    typer.echo(f"  {e.line()}")
    typer.echo(f"  author's captions: {', '.join(video.author_captions) or 'none'}; "
               f"chapters: {len(video.chapters)}")
    typer.echo(f"  folder: {folder}")
    _check_video_limit(e, max_cost)
    if dry_run:
        return
    try:
        download = youtube.download(video, folder, runtimes)
    except youtube.YouTubeError as err:
        fail(str(err))
    typer.echo(f"Downloaded {download.video.name}"
               + (f" + {len(download.captions)} caption file(s)" if download.captions else ""))
    context = video.context() + (chr(10) * 2 + extra_context if extra_context else "")
    settings = settings_for(download.video, f"yt-{youtube.safe_name(video.id, 40)}", context)
    log_to_stderr()
    try:
        report = pipeline.run(settings, progress=print_progress)
        _print_report(report)
        stem = youtube.video_file_stem(video, folder)
        written = _write_outputs(settings, formats, folder, stem, download.video, video.duration)
    except (FileNotFoundError, ValueError, RuntimeError, ConfigurationError, GlossaryError) as err:
        fail(str(err))
    youtube.mark_done(folder, [w.path.name for w in written],
                      {"claude_usd": report.cost_usd, "stt_usd": report.stt_cost_usd})
