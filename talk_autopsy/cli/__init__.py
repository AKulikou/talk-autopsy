"""
Command line (typer): the commands are plain functions in the modules of this
package, registered here in the order `--help` lists them.

- audio.py       `cut`, `transcribe`, `align`
- stages.py      `fix`, `glossary-auto`, `translate`, `srt` (one stage at a time; experiments)
- runs.py        `run` (the pipeline on a file, a YouTube video or a playlist)
- system.py      `doctor`, `web`
- evaluate.py    `eval`
- references.py  `adjudicate`, `resolve`, `judge`, `judge-compare`, `judge-refs`, `glossary-ref`
- common.py      errors, logging, shared options, test-set paths

Only argument parsing and printing live here; the stages are plain functions in
the package, which the web UI (talk_autopsy/web/) calls the same way.
"""

import typer

from talk_autopsy import config
from talk_autopsy.cli import audio, evaluate, references, runs, stages, system
from talk_autopsy.cli.runs import STEPS, is_url, parse_steps

__all__ = ["STEPS", "app", "is_url", "main", "parse_steps"]

app = typer.Typer(
    help="English technical talks to subtitles in a target language.",
    no_args_is_help=True,
    add_completion=False,
)

app.command()(audio.cut)
app.command()(audio.transcribe)
app.command(name="fix")(stages.fix_command)
app.command(name="glossary-auto")(stages.glossary_auto_command)
app.command(name="translate")(stages.translate_command)
app.command(name="srt")(stages.srt_command)
app.command(name="align")(audio.align_command)
app.command()(runs.run)
app.command()(system.doctor)
app.command()(system.web)
app.command(name="eval")(evaluate.eval_command)
app.command(name="adjudicate")(references.adjudicate_command)
app.command(name="resolve")(references.resolve_command)
app.command(name="judge")(references.judge_command)
app.command(name="judge-compare")(references.judge_compare_command)
app.command(name="judge-refs")(references.judge_refs_command)
app.command(name="glossary-ref")(references.glossary_ref_command)


def main() -> None:
    """
    The entry point of `talk-autopsy` and `python -m talk_autopsy`: load .env, then
    run the CLI. Tests invoke `app` directly, so they never read a local .env.
    """
    config.load_environment()
    app()
