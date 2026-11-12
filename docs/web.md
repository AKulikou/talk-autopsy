# Web UI (v1.1) — design note

A local web page that replaces the console launcher: paste a YouTube link or
pick a file, see the estimate, start, watch the steps, open the result folder.
One user, one machine, one job at a time. Session 1 of 3 builds the server and a
plain page; this note is kept in step with the code.

## Architecture

```
talk-autopsy web  ->  uvicorn on 127.0.0.1:<port>  ->  FastAPI app (web/app.py)
   page: web/static/ (one HTML file, vanilla JS/CSS, strings/<lang>.json; no build, no CDN)
   POST /api/estimate -> web/preview.py: metadata (yt-dlp or ffprobe), cost, time (time_estimate.py)
   POST /api/jobs     -> web/jobs.py: JobRunner, one worker thread
        worker: [download] -> pipeline.run(progress=...) -> outputs.write_outputs -> finished
        core progress callbacks -> append-only event list (ids 1, 2, 3, ...)
   GET /api/jobs/{id}/events -> Server-Sent Events, replay from ?after=<id> or Last-Event-ID
```

`web/jobs.py` does not import FastAPI; the core is reached through a small
`Core` object (download, pipeline, outputs) that tests replace with a fake.
Nothing in the frozen `pipeline-v3` paths changes: the web code only listens to
the progress callback the pipeline already has and reads the files each step
already writes.

## Events the server streams

Every event: `{"id": int, "type": str, "t": seconds since the job started, ...}`.

| type | payload | from |
|---|---|---|
| `step_started` | `step` | pipeline `step-start` (`audio`, `stt`, `glossary`, `stt2`, `align`, `fix`, `translate`); the job itself for `download` and `outputs` |
| `step_progress` | `step`, `position`, `total`, `unit` (`block` / `chunk`) | pipeline `block-done` ("block n/total": `fix`, `translate`); chunked Gemini `chunk-*` (fallback engine only) |
| `step_finished` | `step`, `skipped`, `seconds`, `cost_usd`, `stt_cost_usd`, `tokens` (when known) | pipeline `step-done` / `step-skipped`; costs read from the event's text; tokens from the step's output file (`fix`, `translate`) |
| `text` | `kind` (`recognized` / `edit` / `translated`), `segment`, `start`, `text` (and `before` for an edit) | the step's output file, read once the step has finished (`raw.json`, `fixed.json` edits, `<lang>.json`) — not live |
| `estimate_updated` | `claude_usd` and/or `time` (`seconds_left`, `source`) | pipeline `estimate` events; the time estimator (start, step ends, a 30 s tick) |
| `notice` | `text` | pipeline `fallback` and `srt` lines, output writer messages, a progress-display error |
| `finished` | `folder`, `files` (`name`, `format`, `purpose`), `cost_usd`, `stt_cost_usd` | the job, after the outputs are written |
| `failed` | `reason` (one short line), `hint` | any exception, mapped to a plain message; secret values are masked |

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
| `GET /api/jobs/{id}/events?after=<id>` | SSE stream with replay and a heartbeat comment every 15 s |
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
- Errors are one short line: no stack trace, no environment values, no key
  values (the job masks every secret value it knows before an event is stored).

## Later sessions

- Session 2: the rich waiting screen (live transcript, highlighted correction
  edits, translation side by side), an in-browser player with VTT subtitles, a
  desktop shortcut that starts `talk-autopsy web`.
- Session 3: dead code out of frozen modules, `cli.py` split (the glossary
  helpers the web runner borrows from it move to a shared module), screenshots,
  README, history rewrite, tag `v1.1`.
