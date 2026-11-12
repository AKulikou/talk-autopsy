# Web UI (v1.1) — design note

A local web page that replaces the console launcher: paste a YouTube link or
pick a file, see the estimate, start, watch the steps, open the result folder.
One user, one machine, one job at a time. Session 1 of 3 (2026-11-12) built the
server and a plain page; this note describes the code as built.

## Architecture

```
talk-autopsy web (web/server.py)  ->  uvicorn on 127.0.0.1:8765 (or the next free port)
   ->  LocalGuard (Host, Origin, token)  ->  FastAPI app (web/app.py)
   page: web/static/ (index.html, app.css, app.js + api, i18n, events, steps, textlog modules,
         strings/<lang>.json; no build step, no CDN)
   POST /api/estimate -> JobRunner.preview: metadata (yt-dlp or ffprobe), cost (youtube.estimate),
                         time (time_estimate.py)
   POST /api/jobs     -> web/jobs.py: JobRunner, cost gate, one worker thread
        worker: [download] -> pipeline.run(progress=...) -> outputs.write_outputs -> finished
        core progress callbacks -> append-only event list (ids 1, 2, 3, ...)
   GET /api/jobs/{id}/events -> Server-Sent Events, replay from ?after=<id> or Last-Event-ID
```

`web/jobs.py` does not import FastAPI; the core is reached through a small
`Core` object (download, pipeline, outputs) that tests replace with a fake.
Nothing in the frozen `pipeline-v3` paths changes: the web code only listens to
the progress callback the pipeline already has and reads the files each step
already writes. Logs: `logs/web/web-<date>.log` (git-ignored); the console shows
the address and warnings only.

Time estimate (`time_estimate.py`): seconds of work per second of video for each
step, from this machine's history (`%LOCALAPPDATA%	alk-autopsy	ime-history.json`,
keyed by engine, models, compute type, device and core count), else built-in
values measured on the operational runs (flagged `first_run_rough`; download and
the subtitle-track MP4 have none and are listed as not counted); after a minute
of real work the rest is rescaled by the measured speed. Output steps are kept
apart in the history (`outputs_burn`, `outputs_soft`, `outputs_subs`). Cached
steps, a reused download and runs with an engine fallback are not recorded. Any
failure gives "no estimate", never an error.

## Events the server streams

Every event: `{"id": int, "type": str, "t": seconds since the job started, ...}`.

| type | payload | from |
|---|---|---|
| `step_started` | `step`, `detail` (the core's own line; `audio: ...` while the audio is extracted) | pipeline `step-start` (`audio`, `stt`, `glossary`, `stt2`, `align`, `fix`, `translate`); the job itself for `download` and `outputs` |
| `step_progress` | `step`, `position`, `total`, `unit` (`block` / `chunk`) | pipeline `block-done` ("block n/total": `fix`, `translate`); chunked Gemini `chunk-*` (fallback engine only) |
| `step_finished` | `step`, `skipped`, `seconds`, `cost_usd`, `stt_cost_usd`, `tokens` (when known), running `total_cost_usd` / `total_stt_cost_usd` | pipeline `step-done` / `step-skipped`; costs read from the event's text; tokens from the step's output file (`fix`, `translate`) |
| `text` | `kind` (`recognized` / `edit` / `translated`), `segment`, `start`, `text` (and `before` for an edit) | the step's output file, read once the step has finished (`raw.json`, `fixed.json` edits, `<lang>.json`) — not live |
| `estimate_updated` | `claude_usd` + `claude_parts`, and/or `time` (`seconds`, `source`, `first_run_rough`, `excludes`); at the start also `cost` | pipeline `estimate` events ("about X USD" in their text); the time estimator (start, every step end, a 30 s tick when the value moved) |
| `notice` | `code`, `text` | pipeline `fallback` and `srt` lines, output writer messages, a progress-display error |
| `finished` | `folder`, `files` (`name`, `format`, `purpose`), `cost_usd`, `stt_cost_usd`, `fallbacks` | the job, after the outputs are written (costs: the pipeline's own report) |
| `failed` | `reason` (one short line), `hint` (`doctor`, `youtube`, `outputs`, `cost_limit`, `input`, `log`), `step` | any exception, mapped to a plain message; secret values are masked |

Bounds: at most 2,500 text events per kind (then one `text_truncated` notice),
400 characters per text, 20,000 events per job; five finished jobs kept in
memory. The one-job lock is released before the last event, so the page can
start the next video as soon as it sees the end.

Steps with a real position: `fix` and `translate` (blocks), the Gemini fallback
(chunks). Everything else (download, audio, AssemblyAI passes, glossary, align,
srt, burn-in) shows a spinner and elapsed time. The list of missing core events
is in `work/STATUS.md` ("Progress events missing").

## API

| method, path | what |
|---|---|
| `GET /` | the page, with this launch's token in a `<meta>` tag |
| `GET /static/...` | page assets |
| `GET /api/config?ui=<lang>` | target languages (ordered, default, `quality_not_measured`), UI languages, domains, output profiles, per-video cost limit, UI strings |
| `POST /api/estimate` | title, duration, cost, time estimate (or none, `first_run_rough`), files the profile gives |
| `POST /api/jobs` | start a job (cost confirmation when needed) |
| `GET /api/jobs/current` | the running or last job and its state, for a page reload |
| `GET /api/jobs/{id}/events?after=<id>` | SSE stream with replay (also from `Last-Event-ID`), a heartbeat comment every 15 s, closed after the last event |
| `POST /api/pick-file` | native file dialog on this machine; `{"available": false}` without a display |
| `POST /api/open-folder` | open a folder in the file manager, only inside the output root |
| `POST /api/settings` | remember the default target and UI language (git-ignored `web.local.json`) |
| `GET /api/doctor` | the `doctor` checks; key names with set / not set only |

Requests: `mode` = `url` or `file` with exactly one of `url` / `path`;
`profile` = `tv` (burned-in `.<lang>.mp4` + `.srt`), `laptop` (`.srt`, `.ass`,
`.bilingual.ass`, `.subs.mp4`) or `all`; `domain` = `none` / `web3`;
`target_lang` from the offered list. Playlists are refused (use the console
launcher or `talk-autopsy run`).

## Security model

The server can spend money and open folders, so the browser is treated as hostile.

- Bound to `127.0.0.1` only; there is no option to bind elsewhere.
- `Host` must be `127.0.0.1:<port>` or `localhost:<port>` (DNS rebinding).
- Every POST carries the per-launch random token (`X-Talk-Autopsy-Token`); the
  token is in the page only. `Origin`, when present, must be this server. No
  CORS headers, so another site cannot read any response.
- The cost gate lives in the job runner: above the per-video limit (3 USD, Claude
  + speech recognition) or with no estimate, a job starts only if the request
  sends back that exact estimate (or confirms "no estimate").
- Local paths: absolute, existing, a regular file with a media extension; the
  server reads the file in place and never serves or executes a client path.
  `open-folder` opens only folders inside the output root.
- Errors are one short line with a code: no stack trace, no environment
  values, no key values (the job masks every secret value it knows before an
  event is stored).
- The page has a strict Content-Security-Policy (`'self'` only, no frames).

The page: two tabs for the source ("YouTube link" / "File on this computer";
switching keeps what was entered, the request carries only the active one);
the file comes from the native dialog (Tk's, which is the Windows dialog, in a
separate process), a typed path only when no dialog is available. A reload
reattaches to a running job by replaying its events. No fake progress: a bar
only where the core reports a position; the remaining time is the server's last
value, not a client countdown.

## Record and replay

Every job started from the page is recorded to `logs/web/events/<start>-<job>.jsonl`
(git-ignored; the last 20 are kept): a header line with the job summary, then
every event as stored (secret values already masked). Recordings of real videos
hold their transcript text and stay local.

`talk-autopsy web --replay <file> [--speed N]` (hidden from `--help`) plays a
recording through the normal job and Server-Sent Events path: no pipeline, no
network, no cost. `--speed 1` is natural speed, `10` ten times faster, `0` all
at once. The page works as usual: the estimate takes the recording's title and
duration (a link or file name containing `long` gives four hours, to show the
over-limit confirmation); "Start" plays the recording whatever the form says;
the result is a demo folder in a temporary directory with the subtitles rebuilt
from the recorded text and, if ffmpeg is installed, a test-pattern video of the
talk's length. Committed recordings (invented talk, no real text):
`tests/fixtures/replay/synthetic-talk.jsonl` (finished) and
`synthetic-talk-failed.jsonl` (fails in translation); regenerate with
`uv run python -m tests.make_replay_fixture`.

## Later sessions

- Session 2: the rich waiting screen (live transcript, highlighted correction
  edits, translation side by side), an in-browser player with VTT subtitles, a
  desktop shortcut that starts `talk-autopsy web`.
- Session 3: dead code out of frozen modules, `cli.py` split (the glossary
  helpers the web runner borrows from it move to a shared module), screenshots,
  README, history rewrite, tag `v1.1`.
