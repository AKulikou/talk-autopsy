"""This machine: `doctor` (what the tool needs) and `web` (the local web page)."""

from pathlib import Path
from typing import Annotated

import typer

from talk_autopsy import youtube


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
