"""
The local web application: one page and a small JSON API (docs/web.md).

    GET  /                          the page, with this launch's token
    GET  /static/...                its assets
    GET  /api/hello                 {"app": "talk-autopsy"}: a second start finds this one and reuses it
    GET  /api/config?ui=<lang>      languages, domains, profiles, cost limit, strings
    POST /api/settings              remember the default target / interface language
    POST /api/estimate              metadata, cost and time of a link or a file
    POST /api/jobs                  start a job (cost gate in the runner)
    POST /api/link                  what a YouTube link names: video, playlist, video_in_playlist
    POST /api/course/preview        a playlist's lessons, "already done", estimate (web/courses.py)
    POST /api/course/start          start a course (cost gate on the selected lessons' total)
    GET  /api/course/current        the running or last course, for the course header and a reload
    POST /api/course/stop           stop after the current lesson
    GET  /api/course/unfinished     courses left running (server closed) or stopped with lessons left
    GET  /api/course/glossary/{id}  a course's glossary file, as text
    GET  /api/jobs/current          the running or last job, for a page reload
    GET  /api/jobs/{id}/events      Server-Sent Events, replay after ?after=<id> or Last-Event-ID
    GET  /api/jobs/{id}/transcript  a window of the waiting screen's rows (web/transcript.py);
                                    ?rows=false: the summary only (text panel collapsed)
    GET  /api/results               recent finished results in the output root (web/results.py)
    GET  /api/results/{id}          one result: files, the media to play, subtitle tracks
    GET  /api/results/{id}/media/{name}  the video or audio, with HTTP Range (seeking)
    GET  /api/results/{id}/vtt/{name}    an .srt as WebVTT for the player
    POST /api/pick-file             the native file dialog on this machine
    POST /api/open-folder           a folder inside the output root, in the file manager
    GET  /api/doctor                the doctor checks (key names, set / not set)

The server can spend money and open folders, so the browser is treated as
hostile (`LocalGuard`): the Host header must name this server (DNS
rebinding), a cross-origin request is refused (`Origin`, and `Sec-Fetch-Site`
for everything but the page itself, so another site cannot even embed the
media), every POST carries the per-launch token that only the page has, and no
CORS header is ever sent.
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from talk_autopsy import youtube
from talk_autopsy.web import course_glossary, courses, dialogs, jobs, results
from talk_autopsy.web import settings as web_settings
from talk_autopsy.web.courses import CourseRequest, CourseRunner
from talk_autopsy.web.jobs import BusyError, GateError, JobRequest, JobRunner
from talk_autopsy.web.sources import InputError, link_kind
from talk_autopsy.web.transcript import TYPES as TEXT_TYPES

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
TOKEN_HEADER = "x-talk-autopsy-token"
TOKEN_PLACEHOLDER = "__TALK_AUTOPSY_TOKEN__"
HEARTBEAT_S = 15.0
APP_ID = "talk-autopsy"
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


class RevalidatedStatic(StaticFiles):
    """Page assets the browser must revalidate (ETag) before reuse, so an update is never half-loaded."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


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
        problem = self._problem(scope.get("method", "GET"), headers, scope.get("path", ""))
        if problem is not None:
            status, code, message = problem
            logger.warning("refused %s %s: %s", scope.get("method"), scope.get("path"), code)
            return await error(status, code, message)(scope, receive, send)
        return await self.app(scope, receive, send)

    def _problem(self, method: str, headers: dict[str, str], path: str = "") -> tuple[int, str, str] | None:
        if headers.get("host", "").lower() not in self.hosts:
            return 400, "bad_host", "This server answers only on this computer's own address."
        origin = headers.get("origin")
        if origin is not None and origin.lower() not in self.origins:
            return 403, "cross_origin", "Requests from other sites are not accepted."
        site = headers.get("sec-fetch-site")
        if path != "/" and site is not None and site not in ("same-origin", "none"):
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


def _validate_course(payload) -> CourseRequest | JSONResponse:
    if not isinstance(payload, dict):
        return error(422, "bad_request", "The request is not valid.")
    try:
        return CourseRequest.model_validate(payload)
    except (ValidationError, InputError) as e:
        problem = jobs.input_error(e)
        if problem is not None:
            return error(422, problem.code, str(problem))
        return error(422, "bad_request", "The request is not valid.")


def create_app(port: int, runner: JobRunner | None = None, token: str | None = None,
               picker: Callable[..., dialogs.PickResult] = dialogs.pick_file,
               opener: Callable[[Path], None] = dialogs.open_folder,
               doctor: Callable[[], list] = youtube.doctor, local_settings: Path | None = None,
               heartbeat_s: float = HEARTBEAT_S, course_runner: CourseRunner | None = None) -> FastAPI:
    runner = runner or JobRunner()
    course_runner = course_runner or CourseRunner(runner)
    token = token or secrets.token_urlsafe(32)
    app = FastAPI(title="Talk Autopsy", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token
    app.state.runner = runner
    app.state.courses = course_runner

    def busy_now() -> bool:
        return runner.active_id is not None or course_runner.active is not None

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

    @app.get("/api/hello")
    def hello():
        return {"app": APP_ID}

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
        result["busy"] = busy_now()
        return result

    @app.post("/api/link")
    def link(payload=Body(...)):  # noqa: B008
        url = payload.get("url") if isinstance(payload, dict) else None
        if not isinstance(url, str):
            return error(422, "bad_request", "The request is not valid.")
        try:
            return {"kind": link_kind(url)}
        except InputError as e:
            return error(422, e.code, str(e))

    @app.post("/api/course/preview")
    def course_preview(payload=Body(...)):  # noqa: B008
        request = _validate_course(payload)
        if isinstance(request, JSONResponse):
            return request
        try:
            preview = course_runner.preview(request)
        except InputError as e:
            return error(422, e.code, str(e))
        except youtube.YouTubeError as e:
            return error(502, "youtube", jobs.short(str(e).splitlines()[0], 300))
        return {**preview.as_dict(request.selected, runner.limit_usd), "busy": busy_now()}

    @app.post("/api/course/start", status_code=202)
    def course_start(payload=Body(...)):  # noqa: B008
        request = _validate_course(payload)
        if isinstance(request, JSONResponse):
            return request
        try:
            course = course_runner.start(request)
        except BusyError as e:
            return error(409, "busy", str(e), active_id=e.active_id)
        except GateError as e:
            return error(409, e.code, str(e), amount_usd=e.amount_usd)
        except InputError as e:
            return error(422, e.code, str(e))
        except youtube.YouTubeError as e:
            return error(502, "youtube", jobs.short(str(e).splitlines()[0], 300))
        return {"course": course.summary(runner.limit_usd)}

    @app.get("/api/course/current")
    def course_current():
        course = course_runner.current()
        return {"course": course.summary(runner.limit_usd) if course else None}

    @app.post("/api/course/stop")
    def course_stop(payload=Body(...)):  # noqa: B008
        course_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(course_id, str) or not course_runner.stop_after_current(course_id):
            return error(409, "no_course", "No course is running.")
        return {"ok": True}

    @app.get("/api/course/unfinished")
    def course_unfinished():
        active = course_runner.active
        return {"courses": courses.unfinished(Path(runner.core.output_root()),
                                              active.folder if active else None)}

    @app.get("/api/course/glossary/{folder_id}")
    def course_glossary_text(folder_id: str):
        folder = results.course_folder_of(Path(runner.core.output_root()), folder_id)
        path = course_glossary.course_file(folder) if folder is not None else None
        if path is None or not path.is_file() or path.stat().st_size > 2_000_000:
            return error(404, "no_glossary", "This course has no glossary yet.")
        return Response(path.read_text(encoding="utf-8", errors="replace"),
                        media_type="text/plain; charset=utf-8", headers=media_headers)

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

    media_headers = {"Cross-Origin-Resource-Policy": "same-origin", "X-Content-Type-Options": "nosniff",
                     "Cache-Control": "no-store"}

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
    def transcript(job_id: str, start: int | None = None, count: int = 120, follow: bool = False,
                   rows: bool = True):
        job = runner.get(job_id)
        if job is None:
            return error(404, "no_job", "This job is not known (the server was restarted).")
        if not rows:  # the text panel is collapsed: the summary line and the warning only
            return {"summary": job.view.summary()}
        return job.view.window(start, count, follow=follow or start is None)

    def result_folder(result_id: str) -> Path | None:
        return results.folder_of(Path(runner.core.output_root()), result_id)

    def no_result() -> JSONResponse:
        return error(404, "no_result", "This result is not in the output folder.")

    @app.get("/api/results")
    def recent_results():
        return {"results": results.recent(Path(runner.core.output_root()))}

    @app.get("/api/results/{result_id}")
    def result(result_id: str):
        folder = result_folder(result_id)
        return no_result() if folder is None else results.describe(folder)

    @app.get("/api/results/{result_id}/media/{name}")
    def media(result_id: str, name: str):
        folder = result_folder(result_id)
        path = results.file_in(folder, name) if folder is not None else None
        if path is None or path.suffix.lower() not in results.MEDIA_TYPES:
            return no_result()
        return FileResponse(path, media_type=results.MEDIA_TYPES[path.suffix.lower()], headers=media_headers)

    @app.get("/api/results/{result_id}/vtt/{name}")
    def vtt(result_id: str, name: str):
        folder = result_folder(result_id)
        path = results.file_in(folder, name) if folder is not None else None
        if path is None or path.suffix.lower() != ".srt" or path.stat().st_size > 20_000_000:
            return no_result()
        text = path.read_text(encoding="utf-8", errors="replace")
        return Response(results.srt_to_vtt(text), media_type="text/vtt; charset=utf-8", headers=media_headers)

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

    app.mount("/static", RevalidatedStatic(directory=STATIC_DIR), name="static")
    return LocalGuard(app, port, token)  # type: ignore[return-value]
