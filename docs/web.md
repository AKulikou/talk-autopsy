# Web UI (v1.1) — design note

A local web page that replaces the console launcher: paste a YouTube link or
pick a file, see the estimate, start, watch the text appear step by step, watch
the result in the browser, open the result folder. One user, one machine, one
job at a time. Sessions 1 and 2 of 3 (2026-11-12) built it; this note describes
the code as built.

## Starting it

- Desktop shortcut **Talk Autopsy** (made by `scripts\windows\create-shortcut.ps1`):
  runs `scripts\windows\Talk Autopsy Web.bat`, which starts `talk-autopsy web`
  in a console window and opens the page in the default browser. Closing that
  window stops the server. Starting it again while the page runs opens the
  running page (it asks `/api/hello` on the usual ports) instead of failing on
  the busy port.
- Desktop shortcut **Talk Autopsy (console)**: the console launcher
  (`Talk Autopsy.bat`), still the way to run a playlist or drop a file onto it.
- By hand: `uv run talk-autopsy web [--port N] [--no-browser]`.

## Architecture

```
talk-autopsy web (web/server.py)  ->  uvicorn on 127.0.0.1:8765 (or the next free port)
   ->  LocalGuard (Host, Origin, Sec-Fetch-Site, token)  ->  FastAPI app (web/app.py)
   page: web/static/ (index.html, app.css, app.js + api, i18n, events, steps, transcript, player
         modules, strings/<lang>.json; no build step, no CDN)
   POST /api/estimate -> JobRunner.preview: metadata (yt-dlp or ffprobe), cost (youtube.estimate),
                         time (time_estimate.py)
   POST /api/jobs     -> web/jobs.py: JobRunner, cost gate, one worker thread
        worker: [download] -> pipeline.run(progress=...) -> outputs.write_outputs -> finished
        core progress callbacks -> append-only event list (ids 1, 2, 3, ...)
                                -> TranscriptView (web/transcript.py): rows of the text panel
                                -> logs/web/events/<start>-<job>.jsonl (recording)
   GET /api/jobs/{id}/events     -> Server-Sent Events, replay from ?after=<id> or Last-Event-ID
   GET /api/jobs/{id}/transcript -> a window of rows
   GET /api/results/...          -> web/results.py: recent results, the player's media and subtitles
```

`web/jobs.py` does not import FastAPI; the core is reached through a small
`Core` object (download, pipeline, outputs) that tests replace with a fake.
Logs: `logs/web/web-<date>.log` (git-ignored); the console shows the address
and warnings only (a browser dropping a connection is not logged as an error).

**Where the progress comes from.** `pipeline-v3.1` (an observability-only
exception to the freeze, `docs/decisions.md`) gives the pipeline's events
numbers (estimates, cost, seconds, tokens) and, for each finished correction or
translation block, its text: corrected segments with the model's edits, or
translated sentence units with their English source and segment ids. The
download reports bytes (a yt-dlp progress hook, `youtube.download(progress=)`)
and the burn-in reports seconds of video encoded (`ffmpeg -progress`,
`outputs.write_outputs(position=)`). Everything else (audio extraction, the two
AssemblyAI passes, glossary, align, srt, the subtitle-track MP4) shows a
spinner and its elapsed time.

**Time estimate** (`time_estimate.py`): seconds of work per second of video for
each step, from this machine's history (`%LOCALAPPDATA%\talk-autopsy\time-history.json`,
keyed by engine, models, compute type, device and core count), else built-in
values measured on the operational runs (flagged `first_run_rough`); after a
minute of real work the rest is rescaled by the measured speed; download and
burn-in positions feed it too. Output steps are kept apart in the history
(`outputs_burn`, `outputs_soft`, `outputs_subs`). Cached steps, a reused
download, runs with an engine fallback and replays are not recorded. Any
failure gives "no estimate", never an error.

## Events the server streams

Every event: `{"id": int, "type": str, "t": seconds since the job started, ...}`.

| type | payload | from |
|---|---|---|
| `step_started` | `step`, `detail` (the core's own line; `audio: ...` while the audio is extracted) | pipeline `step-start`; the job itself for `download` and `outputs` |
| `step_progress` | `step`, `position`, `total` (null when unknown), `unit` (`block`, `chunk`, `byte`, `second`) | correction and translation blocks; Gemini chunks (fallback engine); download bytes; burn-in seconds. Bytes and seconds at most every 0.5 s |
| `step_finished` | `step`, `skipped`, `seconds`, `cost_usd`, `stt_cost_usd`, `tokens` (`input`, `output`, `cache_write`, `cache_read`, when known), running `total_cost_usd` / `total_stt_cost_usd` | pipeline `step-done` / `step-skipped` (numbers from the event's data) |
| `transcript` | `source` (`stt` / `stt2`), `replace`, `rows` (`id`, `start`, `text`), `part`, `parts` | the recognised text, read from `raw.json` / `raw2.json` when the step ends (AssemblyAI returns it at once); the second pass replaces the first |
| `corrected` | `position`, `total`, `failed`, `ids`, `segments` (`id`, `text`), `edits` (`id`, `before`, `after`); `from_file` for a cached step | one per correction block, live; from `fixed.json` when the step was cached |
| `translated` | `position`, `total`, `failed`, `ids`, `units` (`id`, `text`, `source`, `source_ids`), `source_ids`; `from_file` for a cached step | one per translation block, live; from `<lang>.json` when cached |
| `estimate_updated` | `claude_usd` + `claude_parts`, and/or `time` (`seconds`, `source`, `first_run_rough`, `excludes`); at the start also `cost` | pipeline `estimate` events; the time estimator (start, every step end, a 30 s tick when the value moved) |
| `notice` | `code`, `text` | pipeline `fallback` and `srt` lines, output writer messages, a progress-display error |
| `finished` | `folder`, `result` (the result id for the player), `files` (`name`, `format`, `purpose`), `cost_usd`, `stt_cost_usd`, `fallbacks` | the job, after the outputs are written (costs: the pipeline's own report) |
| `failed` | `reason`, `hint` (`doctor`, `youtube`, `outputs`, `cost_limit`, `input`, `log`), `step`, `error_type` (for an unexpected error) | any exception, mapped to a plain message; secret values are masked |

The SSE stream sends the three text events as a light `text_changed`
(`kind`, `position`, `total`, `failed`, `source`) without their text; the page
reads the rows it shows from `/api/jobs/{id}/transcript`. Bounds: 400
characters per text, 100 recognised segments per `transcript` event, 20,000
events per job (text stops 100 before), 6,000 segments in the view, windows of
at most 300 segments; five finished jobs kept in memory. The one-job lock is
released before the last event, so the page can start the next video as soon
as it sees the end.

## The waiting screen

Left: the steps (state, elapsed time, a bar where there is a real position,
cost and tokens of each finished step), the remaining time and the running
cost. Right: the text, in two aligned columns.

- **Rows.** `web/transcript.py` folds the events into rows: a row is the
  smallest run of consecutive segments that no sentence unit crosses, so a
  sentence that ends in the next segment and its translation sit on one row; a
  segment split into two units keeps both translations on its row. Before
  translation every segment is its own row.
- **Corrections in place.** A word diff of the recognised text against the
  corrected text: removed words struck through, added words underlined and
  marked (with hidden "removed:" / "added:" words for screen readers, so the
  change is not told by colour alone). The counter is the number of edits the
  model listed. A failed block says so on its rows and keeps its text.
- **Translation.** Rows fill block by block; rows still waiting show a quiet
  "…" (with a hidden "translation not ready yet").
- **Following.** The page fetches a window of 120 segments around the newest
  change (at most every 0.4 s) and scrolls to it. When the reader scrolls up
  (wheel, touch, keys or pointer on the text), it stops moving the text and
  shows "Jump to the latest text"; "Show earlier / later text" moves the
  window by 100 segments. The DOM never holds more than one window.
- **Reload.** A reload during a run reattaches: the steps are rebuilt from the
  replayed events and the text from the current window.
- **Narrow windows** stack the steps above the text (below 56 rem) and the two
  text columns into one (below 40 rem).

## The result

The Done screen has a player: an HTML5 `<video>` with two subtitle tracks
(the target language on, English off), served as WebVTT converted from the
result's `.srt` on request (no extra pipeline output). It plays the video
without burned-in subtitles when there is one (the downloaded original
`<stem>.mp4`, then the subtitle-track `<stem>.subs.mp4`); with only the
burned-in `<stem>.<lang>.mp4` the tracks start off and a note says why. A file
input's original video lives outside the output folder and is not served, so a
file-mode result plays one of the produced MP4s. An audio-only input is linked
(or copied) into its result folder and gets an `<audio>` player with its text
beside it, following the audio (a click on a line seeks there).

The start page lists the last finished results (title, date, target language)
with "Watch" and "Open folder": a read-only scan of the folders directly inside
the output root that hold `talk-autopsy-result.json` (written by every
finished web job) or `done.json` (CLI YouTube runs); at most 300 folders
scanned, 12 listed. After a reload the last result is reachable from there.

## API

| method, path | what |
|---|---|
| `GET /` | the page, with this launch's token in a `<meta>` tag; `?ui=<lang>` picks the interface language without saving it |
| `GET /static/...` | page assets (`Cache-Control: no-cache`: revalidated, so an update is never half-loaded) |
| `GET /api/hello` | `{"app": "talk-autopsy"}`, for a second start to find this one |
| `GET /api/config?ui=<lang>` | target languages, UI languages, domains, output profiles, per-video cost limit, UI strings |
| `POST /api/estimate` | title, duration, cost, time estimate, files the profile gives |
| `POST /api/jobs` | start a job (cost confirmation when needed) |
| `GET /api/jobs/current` | the running or last job and its state, for a page reload |
| `GET /api/jobs/{id}/events?after=<id>` | SSE stream with replay (also from `Last-Event-ID`), a heartbeat comment every 15 s, closed after the last event |
| `GET /api/jobs/{id}/transcript?start=&count=&follow=` | a window of rows and the summary (counts, newest position) |
| `GET /api/results` | recent results |
| `GET /api/results/{id}` | one result: files, the media to play, its kind, burned-in or not, subtitle tracks |
| `GET /api/results/{id}/media/{name}` | the video or audio, with HTTP Range (seeking) |
| `GET /api/results/{id}/vtt/{name}` | an `.srt` of the result as WebVTT |
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

The server can spend money, open folders and serve files, so the browser is
treated as hostile.

- Bound to `127.0.0.1` only; there is no option to bind elsewhere.
- `Host` must be `127.0.0.1:<port>` or `localhost:<port>` (DNS rebinding).
- Every POST carries the per-launch random token (`X-Talk-Autopsy-Token`); the
  token is in the page only. `Origin`, when present, must be this server, and
  `Sec-Fetch-Site`, when present, must be `same-origin` or `none` for
  everything but the page itself, so another site cannot even embed the media.
  Media and subtitles also carry `Cross-Origin-Resource-Policy: same-origin`.
  No CORS headers, so another site cannot read any response.
- The cost gate lives in the job runner: above the per-video limit (3 USD,
  Claude + speech recognition; `youtube.over_video_limit`, the same check as
  `run --max-cost`) or with no estimate, a job starts only if the
  request sends back that exact estimate (or confirms "no estimate").
- Local input paths: absolute, existing, a regular file with a media
  extension; read in place, never served or executed.
- Served files: a result is one folder name directly inside the output root
  (with a result marker), a file is one name directly inside that folder; both
  are resolved and checked after resolution, only media extensions (and `.srt`
  for WebVTT) are served, and nothing is listed. `open-folder` opens only
  folders inside the output root.
- Errors are one short line with a code: no stack trace, no environment
  values, no key values (the job masks every secret value it knows before an
  event is stored or recorded).
- The page has a strict Content-Security-Policy (`'self'` only, no frames).

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
talk's length. A replay never starts the real page instead (no reuse check).
Committed recordings (an invented talk, no real text):
`tests/fixtures/replay/synthetic-talk.jsonl` (finished) and
`synthetic-talk-failed.jsonl` (fails in translation); regenerate with
`uv run python -m tests.make_replay_fixture`.

## Not in v1.1

No cancel / stop button (the core has no cooperative cancellation); no
playlists in the page. Session 3: dead code out of
frozen modules, `cli.py` split (the glossary helpers the web runner borrows
from it move to a shared module), screenshots, README, history rewrite, tag
`v1.1`.
