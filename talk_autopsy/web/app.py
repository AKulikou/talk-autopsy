"""
The local web application: one page and a small JSON API (docs/web.md).

    GET  /                          the page, with this launch's token
    GET  /static/...                its assets
    GET  /api/config?ui=<lang>      languages, domains, profiles, cost limit, strings
    POST /api/settings              remember the default target / interface language
    POST /api/estimate              metadata, cost and time of a link or a file
    POST /api/jobs                  start a job (cost gate in the runner)
    GET  /api/jobs/current          the running or last job, for a page reload
    GET  /api/jobs/{id}/events      Server-Sent Events, replay after ?after=<id> or Last-Event-ID
    GET  /api/jobs/{id}/transcript  a window of the waiting screen's rows (web/transcript.py)
    POST /api/pick-file             the native file dialog on this machine
    POST /api/open-folder           a folder inside the output root, in the file manager
    GET  /api/doctor                the doctor checks (key names, set / not set)

The server can spend money and open folders, so the browser is treated as
hostile (`LocalGuard`): the Host header must name this server (DNS
rebinding), a cross-origin request is refused, every POST carries the
per-launch token that only the page has, and no CORS header is ever sent.
Errors are one short line with a code; no stack trace, no environment value.
"""

import asyncio
import hmac
import json
import logging
import secrets
from collections.abc import Callable
from pathlib import Path

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from talk_autopsy import youtube
from talk_autopsy.web import dialogs, jobs
from talk_autopsy.web import settings as web_settings
from talk_autopsy.web.jobs import BusyError, GateError, JobRequest, JobRunner
from talk_autopsy.web.sources import InputError
from talk_autopsy.web.transcript import TYPES as TEXT_TYPES

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
TOKEN_HEADER = "x-talk-autopsy-token"
TOKEN_PLACEHOLDER = "__TALK_AUTOPSY_TOKEN__"
HEARTBEAT_S = 15.0
POLL_S = 0.25
SAFE_METHODS = {"GET", "HEAD"}
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; "
                                "img-src 'self' data:; connect-src 'self'; media-src 'self'; "
                                "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def error(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": code, "message": message, **extra}, status_code=status,
                        headers={"Cache-Control": "no-store"})


class LocalGuard:
    """ASGI middleware: Host, Origin and token checks before anything else runs."""

    def __init__(self, app, port: int, token: str):
        self.app = app
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{h}" for h in self.hosts}
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            return  # no websockets
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        problem = self._problem(scope.get("method", "GET"), headers)
        if problem is not None:
            status, code, message = problem
            logger.warning("refused %s %s: %s", scope.get("method"), scope.get("path"), code)
            return await error(status, code, message)(scope, receive, send)
        return await self.app(scope, receive, send)

    def _problem(self, method: str, headers: dict[str, str]) -> tuple[int, str, str] | None:
        if headers.get("host", "").lower() not in self.hosts:
            return 400, "bad_host", "This server answers only on this computer's own address."
        origin = headers.get("origin")
        if origin is not None and origin.lower() not in self.origins:
            return 403, "cross_origin", "Requests from other sites are not accepted."
        if method not in SAFE_METHODS:
            sent = headers.get(TOKEN_HEADER, "")
            if not sent or not hmac.compare_digest(sent.encode(), self.token.encode()):
                return 403, "bad_token", "Reload the page (the server was restarted)."
        return None


def outgoing(event: dict) -> dict:
    """
    The event as the page gets it: text events (transcript, corrected, translated)
    only say that the text changed; the page reads the rows it shows from
    /api/jobs/{id}/transcript, so an hour-long talk never floods the stream.
    """
    if event.get("type") not in TEXT_TYPES:
        return event
    light = {"id": event["id"], "type": "text_changed", "t": event.get("t"), "kind": event["type"]}
    for key in ("position", "total", "failed", "source"):
        if key in event:
            light[key] = event[key]
    return light


def _validate(payload) -> JobRequest | JSONResponse:
    if not isinstance(payload, dict):
        return error(422, "bad_request", "The request is not valid.")
    try:
        return JobRequest.model_validate(payload)
    except (ValidationError, InputError) as e:
        problem = jobs.input_error(e)
        if problem is not None:
            return error(422, problem.code, str(problem))
        return error(422, "bad_request", "The request is not valid.")


def create_app(port: int, runner: JobRunner | None = None, token: str | None = None,
               picker: Callable[..., dialogs.PickResult] = dialogs.pick_file,
               opener: Callable[[Path], None] = dialogs.open_folder,
               doctor: Callable[[], list] = youtube.doctor, local_settings: Path | None = None,
               heartbeat_s: float = HEARTBEAT_S) -> FastAPI:
    runner = runner or JobRunner()
    token = token or secrets.token_urlsafe(32)
    app = FastAPI(title="Talk Autopsy", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token
    app.state.runner = runner

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, exc: RequestValidationError):
        return error(422, "bad_request", "The request is not valid.")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return error(exc.status_code, "not_found" if exc.status_code == 404 else "http_error",
                     "Not found." if exc.status_code == 404 else "The request was refused.")

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        logger.exception("unexpected error in %s %s", request.method, request.url.path)
        return error(500, "internal", "Unexpected error; the details are in the log file.")

    @app.get("/", include_in_schema=False)
    def page():
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8").replace(TOKEN_PLACEHOLDER, token)
        return HTMLResponse(html, headers=SECURITY_HEADERS)

    @app.get("/api/config")
    def config_(ui: str | None = None):
        local = web_settings.load_local(local_settings)
        languages = web_settings.languages_for_page(local)
        ui_lang = ui if ui in web_settings.ui_codes() else languages["ui_default"]
        return {
            "languages": languages,
            "ui_lang": ui_lang,
            "strings": web_settings.ui_strings(ui_lang),
            "domains": list(web_settings.DOMAINS),
            "profiles": [{"id": k, "formats": list(v)} for k, v in web_settings.PROFILES.items()],
            "cost_limit_usd": runner.limit_usd,
        }

    @app.post("/api/settings")
    def save_settings(payload=Body(...)):  # noqa: B008  (FastAPI's way to read a JSON body)
        if not isinstance(payload, dict):
            return error(422, "bad_request", "The request is not valid.")
        updates = {}
        if payload.get("target_lang") in web_settings.target_codes():
            updates["target_lang"] = payload["target_lang"]
        if payload.get("ui_lang") in web_settings.ui_codes():
            updates["ui_lang"] = payload["ui_lang"]
        if not updates:
            return error(422, "bad_request", "Nothing to remember.")
        return {"saved": web_settings.save_local(updates, local_settings)}

    @app.post("/api/estimate")
    def estimate(payload=Body(...)):  # noqa: B008
        request = _validate(payload)
        if isinstance(request, JSONResponse):
            return request
        try:
            preview = runner.preview(request)
        except InputError as e:
            return error(422, e.code, str(e))
        except youtube.YouTubeError as e:
            return error(502, "youtube", jobs.short(str(e).splitlines()[0], 300))
        result = preview.as_dict(runner.limit_usd)
        result["steps"] = jobs.job_steps(request.mode, preview.formats)
        result["busy"] = runner.active_id is not None
        return result

    @app.post("/api/jobs", status_code=202)
    def start(payload=Body(...)):  # noqa: B008
        request = _validate(payload)
        if isinstance(request, JSONResponse):
            return request
        try:
            job = runner.start(request)
        except BusyError as e:
            return error(409, "busy", str(e), active_id=e.active_id)
        except GateError as e:
            return error(409, e.code, str(e), amount_usd=e.amount_usd)
        except InputError as e:
            return error(422, e.code, str(e))
        except youtube.YouTubeError as e:
            return error(502, "youtube", jobs.short(str(e).splitlines()[0], 300))
        return {"job": job.summary(runner.limit_usd)}

    @app.get("/api/jobs/current")
    def current():
        job = runner.current()
        return {"job": job.summary(runner.limit_usd) if job else None}

    @app.get("/api/jobs/{job_id}/events")
    async def events(job_id: str, request: Request, after: int = 0):
        job = runner.get(job_id)
        if job is None:
            return error(404, "no_job", "This job is not known (the server was restarted).")
        last_id = request.headers.get("last-event-id", "")
        start_after = max(after, int(last_id) if last_id.isdigit() else 0)

        async def stream():
            sent = start_after
            loop = asyncio.get_running_loop()
            beat = loop.time()
            yield "retry: 3000\n\n"
            while True:
                batch, ended = job.events_after(sent)
                for event in batch:
                    yield f"id: {event['id']}\ndata: {json.dumps(outgoing(event), ensure_ascii=False)}\n\n"
                    sent = event["id"]
                if ended and not job.events_after(sent)[0]:
                    return
                if loop.time() - beat >= heartbeat_s:
                    beat = loop.time()
                    yield ": heartbeat\n\n"
                if await request.is_disconnected():
                    return
                await asyncio.sleep(POLL_S)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/jobs/{job_id}/transcript")
    def transcript(job_id: str, start: int | None = None, count: int = 120, follow: bool = False):
        job = runner.get(job_id)
        if job is None:
            return error(404, "no_job", "This job is not known (the server was restarted).")
        return job.view.window(start, count, follow=follow or start is None)

    @app.post("/api/pick-file")
    def pick_file(payload=Body(default=None)):  # noqa: B008
        strings = web_settings.ui_strings((payload or {}).get("ui_lang", "") if isinstance(payload, dict)
                                          else "")
        result = picker(title=strings.get("pick_title", "Choose a video"),
                        media_label=strings.get("pick_media", "Video or audio"),
                        all_label=strings.get("pick_all", "All files"))
        return {"available": result.available, "path": result.path, "busy": result.busy}

    @app.post("/api/open-folder")
    def open_folder(payload=Body(...)):  # noqa: B008
        text = payload.get("path") if isinstance(payload, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text) > 1000 or "\x00" in text:
            return error(422, "bad_path", "The path is not valid.")
        try:
            root = Path(runner.core.output_root()).resolve()
            folder = Path(text.strip()).resolve()
        except (OSError, RuntimeError):
            return error(422, "bad_path", "The path is not valid.")
        if not folder.is_relative_to(root) or not folder.is_dir():
            return error(403, "outside_output", "Only folders inside the output folder can be opened.")
        try:
            opener(folder)
        except Exception as e:
            logger.warning("open folder failed (%s)", type(e).__name__)
            return error(500, "open_failed", "The folder could not be opened.")
        return {"ok": True}

    @app.get("/api/doctor")
    def doctor_():
        checks = doctor()
        return {"checks": [{"name": c.name, "ok": c.ok, "required": c.required, "detail": c.detail,
                            "fix": c.fix} for c in checks],
                "ok": all(c.ok or not c.required for c in checks)}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return LocalGuard(app, port, token)  # type: ignore[return-value]
