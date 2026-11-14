"""
Screenshots of the web UI from synthetic data only, for the README (docs/img/).

    uv run python scripts/web_screenshots.py                 # English -> docs/img/
    uv run python scripts/web_screenshots.py --ui lt --out work/screenshots-lt
    uv run python scripts/web_screenshots.py --ui ru --set all --zoom 1.5 --out work/screenshots-ru-150

Needs Microsoft Edge or Google Chrome (driven headless over the DevTools
protocol, with the `websockets` package already in the environment) and
ffmpeg. Nothing real is shown: the server runs in replay mode on the committed
recordings of an invented talk (tests/fixtures/replay/), in a temporary folder
of its own, so the recent-results list holds only the replayed demo; the form
uses link mode, so no local folder appears; the player plays a title card made
here with ffmpeg (not committed). Playback pauses right after chosen events to
catch the waiting screen in a known state. No pipeline, no network beyond
127.0.0.1, no cost.

Screens: form (with the synthetic recent result), estimate with the
over-limit confirmation, running (translation block 1 of 2: a real progress
bar, corrections highlighted, half the translation), running during a step
without a position (a spinner), done with the player and subtitles, failed.
The waiting screen's text panel is opened for every picture (it is collapsed
by default, and the rows are the best picture of what the pipeline does).
`--set untranslated` (or `all`) adds the run whose first translation block was
left in English: the waiting screen right after that block, and the Done screen
with the warning; `--set collapsed` (or `all`) the waiting screen as it opens,
text panel collapsed, in translation block 1. `--set course` (also in `readme`
and `all`): a playlist link gives the replay's invented demo course (web/replay.py): the course screen
with its lessons (one private, skipped), the waiting screen with the course header in lesson 2, the
course's Done screen, and a lesson in the player with "previous / next lesson". `--zoom 1.5` renders as
the browser does at 150% (fewer CSS pixels across, each drawn bigger).
Each PNG is reduced to a palette with ffmpeg when that keeps it smaller.
"""

import argparse
import base64
import itertools
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures" / "replay"
FINISHED = FIXTURES / "synthetic-talk.jsonl"
FAILED = FIXTURES / "synthetic-talk-failed.jsonl"
UNAVAILABLE = FIXTURES / "synthetic-talk-unavailable.jsonl"  # the README picture: a service outage
UNTRANSLATED = FIXTURES / "synthetic-talk-untranslated.jsonl"
LINK = "https://www.youtube.com/watch?v=Synthetic01"
LONG_LINK = "https://www.youtube.com/watch?v=longSynth01"  # "long" in a replay link: four hours
COURSE_LINK = "https://www.youtube.com/playlist?list=PLsyntheticDemo01"  # the replay's demo course
WIDTH, HEIGHT = 1280, 860
BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/microsoft-edge",
]


# --- headless browser over the DevTools protocol --------------------------------------


def find_browser() -> str:
    for name in ("msedge", "google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    for path in BROWSERS:
        if Path(path).is_file():
            return path
    raise SystemExit("No Edge or Chrome found; see docs/web.md (Screenshots) for the manual steps.")


class Browser:
    def __init__(self, profile: Path, port: int = 9339, zoom: float = 1.0):
        from websockets.sync.client import connect

        self.proc = subprocess.Popen(
            [find_browser(), "--headless=new", f"--remote-debugging-port={port}",
             f"--user-data-dir={profile}",
             "--no-first-run", "--disable-extensions", "--hide-scrollbars", "--mute-audio",
             "--no-proxy-server", f"--window-size={WIDTH},{HEIGHT}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(150):
            try:
                targets = json.loads(opener.open(f"http://127.0.0.1:{port}/json", timeout=1).read())
                page = next(t for t in targets if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        else:
            self.proc.terminate()
            raise SystemExit("the browser did not start")
        self.ws = connect(page["webSocketDebuggerUrl"], max_size=50_000_000)  # closed in close()
        self.ids = itertools.count(1)
        self.zoom = zoom  # browser zoom: fewer CSS pixels, each drawn `zoom` times bigger
        self.width, self.height = round(WIDTH / zoom), round(HEIGHT / zoom)
        for method in ("Page.enable", "Runtime.enable"):
            self.call(method)
        self.metrics(self.height)

    def metrics(self, height: int) -> None:
        self.call("Emulation.setDeviceMetricsOverride", width=self.width, height=height,
                  deviceScaleFactor=self.zoom, mobile=False)

    def call(self, method: str, **params):
        msg_id = next(self.ids)
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def go(self, url: str) -> None:
        self.call("Page.navigate", url=url)
        time.sleep(1.0)

    def js(self, expression: str):
        result = self.call("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        if result.get("exceptionDetails"):
            raise RuntimeError(f"js error in {expression!r}: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    def wait(self, expression: str, timeout: float = 60.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.js(expression):
                return
            time.sleep(0.2)
        raise TimeoutError(expression)

    def shot(self, path: Path, height: int) -> Path:
        """
        The page from the top down to `height` CSS pixels. The viewport is made tall enough
        first and given time to settle: capturing beyond the viewport resizes the page during
        the capture, and the video player's captions are caught mid-layout.
        """
        tall = max(self.height, height)
        if tall != self.height:
            self.metrics(tall)
            time.sleep(1.5)
        clip = {"x": 0, "y": 0, "width": self.width, "height": height, "scale": 1}
        data = self.call("Page.captureScreenshot", format="png", clip=clip)["data"]
        path.write_bytes(base64.b64decode(data))
        if tall != self.height:
            self.metrics(self.height)
        return path

    def close(self) -> None:
        try:
            self.ws.close()
        finally:
            self.proc.terminate()
            self.proc.wait(timeout=10)


# --- replay server in this process, pausable ------------------------------------------


def title_card(path: Path, seconds: float) -> None:
    """A plain title card (ffmpeg), where the replay's demo step looks for the video (it keeps one found)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ("drawtext=text='Synthetic demo clip':fontcolor=0xe2e8f0:fontsize=40:x=(w-tw)/2:y=(h-th)/2-110,"
            "drawtext=text='an invented talk, no real video':fontcolor=0x94a3b8:fontsize=24:"
            "x=(w-tw)/2:y=(h-th)/2-55")
    base = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
            "-i", f"color=c=0x1e293b:s=1280x720:r=2:d={seconds:.0f}"]
    tail = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(path)]
    if subprocess.run([*base, "-vf", text, *tail], capture_output=True).returncode != 0:
        subprocess.run([*base, *tail], capture_output=True, check=True)  # no fonts: a plain card


class Server:
    """The web app on 127.0.0.1 with a replay runner whose playback can stop after a chosen event."""

    def __init__(self, recording_path: Path, root: Path, speed: float):
        import uvicorn

        from talk_autopsy.web import replay
        from talk_autopsy.web.app import create_app
        from talk_autopsy.web.server import bind_socket

        recording = replay.load(recording_path)
        final = recording.events[-1]
        if final["type"] == "finished":
            folder = Path(str(final.get("folder") or "replay")).name
            names = [f.get("name", "") for f in final.get("files") or []]
            stem = next((n[:-4] for n in names if n.endswith(".srt") and not n.endswith(".en.srt")), "replay")
            title_card(root / "output" / folder / f"{stem}.mp4", recording.duration_s or 60.0)
        # lessons of the demo course play a title card too, not a test pattern
        replay.make_test_video = lambda path, seconds: (path.is_file() or title_card(path, seconds)) or True
        self.runner = replay.ReplayRunner(recording, speed=speed, root=root)
        self.jobs: list = []
        self.pause_when = None  # event -> bool
        self.paused, self.resume = threading.Event(), threading.Event()
        register = self.runner._register

        def register_pausable(job):
            register(job)
            self.jobs.append(job)
            add = job.add_recorded

            def add_and_maybe_pause(event):
                add(event)
                if self.pause_when is not None and self.pause_when(event):
                    self.pause_when = None
                    self.paused.set()
                    self.resume.wait(120)
                    self.resume.clear()

            job.add_recorded = add_and_maybe_pause

        self.runner._register = register_pausable
        self.sock = bind_socket(8801)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        app = create_app(self.sock.getsockname()[1], runner=self.runner)
        self.server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="off"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.sock]}, daemon=True)
        self.thread.start()
        for _ in range(100):
            if getattr(self.server, "started", False):
                return
            time.sleep(0.1)
        raise SystemExit("the replay server did not start")

    def stop(self) -> None:
        self.resume.set()
        self.server.should_exit = True
        self.thread.join(10)
        self.sock.close()


# --- the screens ----------------------------------------------------------------------


def shrink(path: Path) -> None:
    """A 256-colour palette PNG (ffmpeg) when that is smaller; the UI has few colours."""
    small = path.with_name(path.stem + ".pal.png")
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-vf",
               "split[a][b];[a]palettegen=max_colors=256:stats_mode=full[p];[b][p]paletteuse=dither=none",
               str(small)]
    made = subprocess.run(command, capture_output=True).returncode == 0
    if made and small.stat().st_size < path.stat().st_size:
        small.replace(path)
    small.unlink(missing_ok=True)


def bottom(b: Browser, element_id: str) -> float:
    """The element's bottom edge in page coordinates (CSS pixels)."""
    rect = f"document.getElementById('{element_id}').getBoundingClientRect()"
    return float(b.js(f"{rect}.bottom + window.scrollY"))


def top(b: Browser, element_id: str) -> float:
    rect = f"document.getElementById('{element_id}').getBoundingClientRect()"
    return float(b.js(f"{rect}.top + window.scrollY"))


def start_estimate(b: Browser, base: str, ui: str, link: str) -> None:
    b.go(f"{base}/?ui={ui}")
    b.wait("document.getElementById('estimate')?.textContent.length > 0")
    b.js("document.getElementById('tab-url').click()")
    b.js(f"document.getElementById('url').value = {json.dumps(link)}")
    b.js("document.getElementById('estimate').click()")
    b.wait("!document.getElementById('view-estimate').hidden && "
           "document.getElementById('est-cost').textContent.length > 0")
    time.sleep(0.4)


def text_panel(b: Browser, shown: bool) -> None:
    """Open or close the waiting screen's text panel (the page remembers it in the browser profile)."""
    b.wait("document.getElementById('live-toggle') !== null")
    expanded = b.js("document.getElementById('live-toggle').getAttribute('aria-expanded') === 'true'")
    if expanded != shown:
        b.js("document.getElementById('live-toggle').click()")
    time.sleep(0.6)


def take(b: Browser, out: Path, name: str, shots: list[Path], down_to: str, margin: int = 24) -> None:
    """A screenshot from the top of the page to the bottom of element `down_to` (plus a margin)."""
    b.js("document.activeElement && document.activeElement.blur()")
    b.js("window.scrollTo(0, 0)")
    time.sleep(0.3)
    height = int(bottom(b, down_to)) + margin
    path = b.shot(out / f"{name}.png", height)
    shrink(path)
    shots.append(path)


def finished_flow(b: Browser, srv: Server, ui: str, out: Path, shots: list[Path]) -> None:
    start_estimate(b, srv.url, ui, LONG_LINK)
    assert b.js("!document.getElementById('confirm-box').hidden"), "no over-limit confirmation shown"
    take(b, out, "web-estimate-confirm", shots, "view-estimate")

    b.js("document.getElementById('back').click()")
    start_estimate(b, srv.url, ui, LINK)
    # the waiting screen while a step without a position runs (second speech recognition pass)
    srv.pause_when = lambda e: e["type"] == "step_started" and e.get("step") == "stt2"
    b.js("document.getElementById('start').click()")
    b.wait("!document.getElementById('view-run').hidden")
    text_panel(b, True)
    srv.paused.wait(60)
    srv.paused.clear()
    b.wait("document.querySelectorAll('#live-rows .row').length > 5", 30)
    time.sleep(2.5)  # the step's elapsed time ticks
    take(b, out, "web-running-spinner", shots, "view-run")

    # translation block 1 of 2: a bar, corrections in place, half of the translation
    srv.pause_when = lambda e: e["type"] == "translated" and e.get("position") == 1
    srv.resume.set()
    srv.paused.wait(60)
    srv.paused.clear()
    b.wait("document.querySelectorAll('#live-rows ins, #live-rows del').length > 0", 30)
    time.sleep(1.5)
    b.js("""(() => { const rows = [...document.querySelectorAll('#live-rows .row')];
        const edited = rows.find(r => r.querySelector('ins, del'));
        if (edited) edited.scrollIntoView({block: 'center'}); window.scrollTo(0, 0); })()""")
    time.sleep(0.8)
    take(b, out, "web-running", shots, "view-run")

    srv.resume.set()
    b.wait("!document.getElementById('view-done').hidden", 120)
    b.wait("document.querySelector('#player-media video') && "
           "document.querySelector('#player-media video').readyState >= 1", 30)
    cue_at = b.js("""(() => { const v = document.querySelector('#player-media video');
        const t = [...v.textTracks].find(t => t.mode === 'showing');
        const c = t && t.cues && [...t.cues].find(c => c.text.length > 40 && c.text.length < 90);
        return c ? (c.startTime + c.endTime) / 2 : 30; })()""")
    b.js(f"""new Promise(done => {{ const v = document.querySelector('#player-media video');
        v.addEventListener('seeked', done, {{once: true}}); v.currentTime = {cue_at}; }})""")
    time.sleep(4.0)  # the cue settles above the controls once they have faded in
    # down to the cost line: the files and the result folder (a local path) below it stay out
    assert top(b, "done-folder") > bottom(b, "done-cost") + 16, "the result folder would be in the shot"
    take(b, out, "web-done", shots, "done-cost", margin=16)

    b.js("document.getElementById('another').click()")
    b.wait("!document.getElementById('view-form').hidden && document.querySelectorAll('#recent-list li, "
           "#recent-list .recent-item, #recent-list > *').length > 0", 15)
    b.js("document.getElementById('tab-url').click()")
    b.js(f"document.getElementById('url').value = {json.dumps(LINK)}")
    time.sleep(0.6)
    take(b, out, "web-form", shots, "view-form")


def failed_flow(b: Browser, srv: Server, ui: str, out: Path, shots: list[Path]) -> None:
    start_estimate(b, srv.url, ui, LINK)
    b.js("document.getElementById('start').click()")
    b.wait("!document.getElementById('view-failed').hidden", 120)
    time.sleep(0.6)
    take(b, out, "web-failed", shots, "view-failed")
    if not b.js("document.getElementById('failed-again').hidden"):  # "Run again": the estimate first
        b.js("document.getElementById('failed-again').click()")
        b.wait("!document.getElementById('view-estimate').hidden", 30)


def course_flow(b: Browser, srv: Server, ui: str, out: Path, shots: list[Path]) -> None:
    """The demo course: lessons, the course header while lesson 2 runs, the course done, a lesson."""
    b.go(f"{srv.url}/?ui={ui}")
    b.wait("document.getElementById('estimate')?.textContent.length > 0")
    b.js("document.getElementById('tab-url').click()")
    b.js(f"document.getElementById('url').value = {json.dumps(COURSE_LINK)}")
    b.js("document.getElementById('estimate').click()")
    b.wait("!document.getElementById('view-course').hidden && "
           "document.querySelectorAll('#course-list li').length > 0", 30)
    b.js("document.getElementById('course-glossary').checked = true;"
         "document.getElementById('course-glossary').dispatchEvent(new Event('change'))")
    time.sleep(1.0)
    assert b.js("document.querySelectorAll('#course-list li[data-available=\"false\"]').length === 1")
    take(b, out, "web-course", shots, "view-course")

    # lesson 2, translation block 1: the course header above the usual view
    srv.pause_when = lambda e: len(srv.jobs) >= 2 and e["type"] == "translated" and e.get("position") == 1
    b.js("document.getElementById('course-start').click()")
    b.wait("!document.getElementById('view-run').hidden && "
           "!document.getElementById('course-head').hidden", 30)
    srv.paused.wait(180)
    srv.paused.clear()
    time.sleep(2.5)  # the course poll and the steps catch up
    b.js("document.getElementById('course-head-details').open = true")
    time.sleep(0.5)
    take(b, out, "web-course-running", shots, "steps")

    srv.resume.set()
    b.wait("!document.getElementById('view-course-done').hidden", 300)
    time.sleep(0.8)
    assert top(b, "course-done-folder") > bottom(b, "course-done-list"), "the folder would be in the shot"
    take(b, out, "web-course-done", shots, "course-done-list", margin=16)

    b.js("document.querySelectorAll('#course-done-list .actions-inline button')[1].click()")  # lesson 2
    b.wait("!document.getElementById('view-done').hidden && "
           "!document.getElementById('lesson-nav').hidden", 30)
    b.wait("document.querySelector('#player-media video') && "
           "document.querySelector('#player-media video').readyState >= 1", 30)
    time.sleep(3.0)
    take(b, out, "web-course-lesson", shots, "player-box", margin=16)


def untranslated_flow(b: Browser, srv: Server, ui: str, out: Path, shots: list[Path]) -> None:
    """The run whose first translation block stayed English: the warning while running and when done."""
    start_estimate(b, srv.url, ui, LINK)
    srv.pause_when = lambda e: e["type"] == "translated" and e.get("position") == 1
    b.js("document.getElementById('start').click()")
    b.wait("!document.getElementById('view-run').hidden")
    text_panel(b, True)
    srv.paused.wait(120)
    srv.paused.clear()
    b.wait("!document.getElementById('run-warning').hidden", 30)
    time.sleep(1.0)
    take(b, out, "web-running-untranslated", shots, "view-run")
    srv.resume.set()
    b.wait("!document.getElementById('view-done').hidden", 120)
    b.wait("!document.getElementById('done-warning').hidden", 10)
    time.sleep(2.0)
    take(b, out, "web-done-untranslated", shots, "view-done")


def collapsed_flow(b: Browser, srv: Server, ui: str, out: Path, shots: list[Path]) -> None:
    """The waiting screen with the text panel collapsed (the default): steps, summary line, no rows."""
    start_estimate(b, srv.url, ui, LINK)
    srv.pause_when = lambda e: e["type"] == "translated" and e.get("position") == 1
    b.js("document.getElementById('start').click()")
    b.wait("!document.getElementById('view-run').hidden")
    text_panel(b, False)
    srv.paused.wait(120)
    srv.paused.clear()
    b.wait("document.getElementById('live-status').textContent.includes('·')", 30)
    assert b.js("document.querySelectorAll('#live-rows .row').length === 0"), "rows built while collapsed"
    time.sleep(1.0)
    take(b, out, "web-running-collapsed", shots, "view-run")
    srv.resume.set()
    b.wait("!document.getElementById('view-done').hidden", 120)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ui", default="en", help="Interface language: en, ru or lt.")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "img")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Replay speed; 1 (natural) keeps the elapsed time in line with the step times.")
    parser.add_argument("--set", choices=("readme", "untranslated", "collapsed", "course", "all"),
                        default="readme",
                        help="readme: the README pictures; untranslated: the warning screens; "
                             "collapsed: the waiting screen with the text panel collapsed; all: every one.")
    parser.add_argument("--zoom", type=float, default=1.0, help="Browser zoom, e.g. 1.5 for 150%%.")
    args = parser.parse_args()
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is needed (title card, palette PNGs)")
    args.out.mkdir(parents=True, exist_ok=True)
    shots: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="ta-shots-") as tmp:
        tmp = Path(tmp)
        browser = Browser(tmp / "profile", zoom=args.zoom)
        flows = []
        if args.set in ("readme", "all"):
            flows += [(FINISHED, finished_flow), (UNAVAILABLE, failed_flow)]
        if args.set in ("readme", "course", "all"):
            flows += [(FINISHED, course_flow)]
        if args.set in ("untranslated", "all"):
            flows += [(UNTRANSLATED, untranslated_flow)]
        if args.set in ("collapsed", "all"):
            flows += [(FINISHED, collapsed_flow)]
        try:
            for recording, flow in flows:
                speed = max(args.speed, 6.0) if flow is course_flow else args.speed  # six lessons
                srv = Server(recording, tmp / f"{recording.stem}-{flow.__name__}", speed)
                try:
                    flow(browser, srv, args.ui, args.out, shots)
                finally:
                    srv.stop()
        finally:
            browser.close()
    for path in shots:
        print(f"{path.name:<26} {path.stat().st_size / 1024:6.0f} KB")


if __name__ == "__main__":
    main()
