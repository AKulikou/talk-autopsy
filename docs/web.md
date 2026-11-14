# Web UI (v1.1, courses v1.2) — design note

A local web page that replaces the console launcher: paste a YouTube link or
pick a file, see the estimate, start, watch the text appear step by step, watch
the result in the browser, open the result folder. One user, one machine, one
job at a time. This note describes the code as built (v1.1).

## Starting it

- Desktop shortcut **Talk Autopsy** (made by `scripts\windows\create-shortcut.ps1`):
  runs `scripts\windows\Talk Autopsy Web.bat`, which starts `talk-autopsy web`
  in a console window and opens the page in the default browser. Closing that
  window stops the server. Starting it again while the page runs opens the
  running page (it asks `/api/hello` on the usual ports) instead of failing on
  the busy port.
- Desktop shortcut **Talk Autopsy (console)**: the console launcher
  (`Talk Autopsy.bat`), to drop a file onto it. Since v1.2 a playlist runs in
  the page too (Courses, below).
- By hand: `uv run talk-autopsy web [--port N] [--no-browser]`.

## Architecture

```
talk-autopsy web (web/server.py)  ->  uvicorn on 127.0.0.1:8765 (or the next free port)
   ->  LocalGuard (Host, Origin, Sec-Fetch-Site, token)  ->  FastAPI app (web/app.py)
   page: web/static/ (index.html, app.css, app.js + api, i18n, events, steps, transcript, follow, player
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
Logs: `logs/web/web-<date>.log` (git-ignored) has everything, as before. The
console window (the owner keeps it open) shows only the address and the log's
path, one line when a run starts and one when it ends ("11:39 Finished: ok
(0.00 USD)", "Finished with problems (...): see the log", "Failed at fix: see
the log"), and real errors as one line each without the traceback; warnings
go to the log file only (owner, 2026-11-13; `server.ConsoleOnly`). A browser
dropping a connection is not logged as an error.

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

## Courses: a playlist in the page (v1.2)

The owner learns from YouTube courses of many short lessons and uses only the
page. A playlist link opens a course screen; the lessons then run one after
another through the same single-job worker, each an ordinary job with its usual
waiting screen and result files (`web/courses.py`).

```
POST /api/link {url}          -> video | playlist | video_in_playlist
   video_in_playlist: the form asks "only this video / the whole playlist"
POST /api/course/preview      -> CourseRunner.preview: yt-dlp flat extraction (one request; titles,
                                 durations, availability), up to 100 lessons; per lesson "already done"
                                 and the estimate counting the cache (JobRunner.preview with the lesson
                                 folder); the totals of the selection
POST /api/course/start        -> cost gate on the selected lessons' total -> a course thread that holds
                                 the one-job lock from the first lesson to the last:
     for each selected lesson:  full metadata (the context, as for a single video) -> a lesson job
                                 (JobRunner.run_lesson, the usual events) -> its status -> course glossary
GET  /api/course/current      -> the course header (polled every 1.5 s) and a page reload
POST /api/course/stop         -> stop after the current lesson
GET  /api/course/unfinished   -> courses to continue on the start page
GET  /api/course/glossary/{id}-> the course glossary file as text
```

- **Course screen.** The lessons in playlist order with a checkbox each (all
  available ones checked; "Select all", "Clear all", "from lesson N to lesson
  M"), the length, "already done" for a lesson whose folder has its done marker
  or whose every paid step is in the cache (the estimate reads the context the
  earlier run stored in `work/yt-<id>/meta.json`; a flat playlist entry has no
  description, so its own context would look stale), and greyed lessons that
  cannot run with the reason (private, deleted, unavailable, no length). More
  than 100 entries: the first 100, with a note. The profile, the language and
  "about smart contracts" are those of the form, set once for the course; the
  course screen states them. The totals (count, money, time) come from the
  server for every selection change (it keeps the playlist it read for an hour),
  so the page and the gate use the same amount. Above the per-video limit
  (3 USD) **in total** the usual confirmation is needed, server-side; each
  lesson then runs with the pipeline's usual ceiling (`JobRunner.allowance`).
- **Waiting screen.** A course header above the usual one-video view: the
  course title, "lesson 3 of 12", an overall bar (finished lessons), the money
  of the course so far (the running lesson included), the time left (the queued
  lessons' estimates plus the running lesson's own remaining time), the lessons
  with their status (queued / in progress / done / error / skipped; an error
  with its reason in words, the same sentences as the Failed screen) and "Stop
  after the current lesson" (the lesson finishes; the rest stay queued). A
  failed lesson does not stop the course.
- **Folders.** `<output>/<date> <course title>/NN <lesson title>/`, NN the
  playlist position zero-padded to the playlist's size, names cut at a word.
  The course's service folder holds `course.json` (the request, every lesson's
  folder, status, money and error, the state). Re-submitting the same playlist
  finds the folder by the playlist id and reuses the lesson folders, so a
  finished lesson comes from the cache in seconds. A video downloaded before
  for a single run is hard-linked into the lesson folder (same drive), so it is
  not downloaded again and its cached steps stay valid.
- **Done.** The course's Done screen lists the lessons with their status and
  "Watch", "Retry the failed ones" (the course screen again with only those
  checked: the same estimate and gate), "Continue" (lessons left queued after a
  stop) and "Open the course folder". A lesson in the player has "← previous
  lesson / next lesson →", its place in the course and "Open the course
  folder"; the subtitle choice (target language / English / off) is kept from
  one lesson to the next (`subs.initialTrack`, tested in Node). The start
  page's recent results show a course as one entry with its lessons inside; a
  lesson's result id is `<course folder>|<lesson folder>` (`|` is in no Windows
  file name; both parts are resolved and checked like a single folder).
- **Restart safety.** A course whose `course.json` still says `running` (the
  server window was closed mid-course) or `stopped` with lessons left is listed
  on the start page under "Unfinished courses" with "Continue": the course
  screen with the remaining lessons checked.
- **Course glossary** (`web/course_glossary.py`, C2b in `docs/experiments.md`):
  an option on the course screen; after every finished lesson the terms of its
  auto-glossary go into `<course>/course-glossary.md` (with the Russian
  rendering the lesson used, where it used the auto-glossary's suggestion), and
  the next lessons get it as the last part of their user glossary. Each lesson
  keeps the exact glossary it ran with, so a re-run hits the cache. **Off by
  default** since C2b (2026-11-14: 2 inconsistent terms on lessons 1-3 of a
  real course without it and 2 with it; the pre-registered rule asked for 0).

Tests: `tests/test_web_courses.py` (fake yt-dlp and pipeline: links, lessons
and unavailable ones, folder names, more than 100, the gate on the total,
order, a failure in the middle, selection, stop after current, one job at a
time, resume in the same folders, restart safety, reuse of a download, the
course group and previous / next, the API end to end) and `tests/js/course.test.mjs`.

## Events the server streams

Every event: `{"id": int, "type": str, "t": seconds since the job started, ...}`.

| type | payload | from |
|---|---|---|
| `step_started` | `step`, `detail` (the core's own line; `audio: ...` while the audio is extracted) | pipeline `step-start`; the job itself for `download` and `outputs` |
| `step_progress` | `step`, `position`, `total` (null when unknown), `unit` (`block`, `chunk`, `byte`, `second`) | correction and translation blocks; Gemini chunks (fallback engine); download bytes; burn-in seconds. Bytes and seconds at most every 0.5 s |
| `step_finished` | `step`, `skipped`, `seconds`, `cost_usd`, `stt_cost_usd`, `tokens` (`input`, `output`, `cache_write`, `cache_read`, when known), running `total_cost_usd` / `total_stt_cost_usd` | pipeline `step-done` / `step-skipped` (numbers from the event's data) |
| `transcript` | `source` (`stt` / `stt2`), `replace`, `rows` (`id`, `start`, `end`, `text`), `part`, `parts` | the recognised text, read from `raw.json` / `raw2.json` when the step ends (AssemblyAI returns it at once); the second pass replaces the first |
| `corrected` | `position`, `total`, `failed`, `ids`, `segments` (`id`, `text`), `edits` (`id`, `before`, `after`); `from_file` for a cached step | one per correction block, live; from `fixed.json` when the step was cached |
| `translated` | `position`, `total`, `failed`, `ids`, `units` (`id`, `text`, `source`, `source_ids`), `source_ids`; `from_file` for a cached step | one per translation block, live; from `<lang>.json` when cached |
| `estimate_updated` | `claude_usd` + `claude_parts`, and/or `time` (`seconds`, `source`, `first_run_rough`, `excludes`); at the start also `cost` | pipeline `estimate` events; the time estimator (start, every step end, a 30 s tick when the value moved) |
| `notice` | `code`, `text` (the core's own line), `message` + `params` (a known kind, `web/notices.py`; null otherwise), `kind` | pipeline `fallback` and `srt` lines, output writer messages, a progress-display error |
| `finished` | `folder`, `result` (the result id for the player), `files` (`name`, `format`, `purpose`), `cost_usd`, `stt_cost_usd`, `cached` (`claude`, `stt`: every paid step of that service came from the cache), `attempts` (`failed`, `claude_usd`, `stt_usd`: the video's total with the failed runs before this one, or null), `fallbacks`, `untranslated` and `uncorrected` (time ranges of failed blocks) | the job, after the outputs are written (costs: the pipeline's own report; ranges: the step outputs' `failed_blocks`; attempts: the result folder's run log) |
| `failed` | `reason` (technical, English), `hint` (`service`, `doctor`, `youtube`, `outputs`, `cost_limit`, `input`, `log`), `step`, `error_type`, for `service` also `service` (`claude`, `assemblyai`, `gemini`, `network`), `kind` (`unavailable`, `overloaded`, `rate_limited`, `timeout`, `connection`) and `status` (HTTP); `cost_usd` and `stt_cost_usd` (spent before the stop, including the paid answers of the step that stopped), `cached` | any exception, mapped to a plain message; secret values are masked |

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
- **Collapsed by default** (owner's decision, 2026-11-13: not needed in
  everyday use). The running screen shows the steps in one column of the
  usual width and, under them, the text's summary line (corrections made,
  phrases translated) and a button, "Show text as it is processed" / "Hide
  text" (a real button with `aria-expanded`, so Tab and Enter or Space work).
  The warnings stay visible either way: the untranslated / uncorrected
  warning with its minutes, the notices and the technical details. While
  collapsed the page asks only for the summary
  (`/api/jobs/{id}/transcript?rows=false`) and builds no rows; opening the
  panel fetches the newest window, which the server folds from every event so
  far, and widens the screen to the two columns. The choice is kept in this
  browser's `localStorage` (`talk-autopsy.live-text`); every read and write is
  in try/catch, so without storage (a private window, blocked site data) the
  panel starts collapsed and the toggle still works until the page is
  reloaded.
- **Following.** The page fetches a window of 120 segments around the newest
  change (at most every 0.4 s) and scrolls to it. When the reader scrolls up
  (wheel, touch, keys or pointer on the text), it stops moving the text and
  enables "Jump to the latest text", the one way back to the newest text.
  "Show earlier text" moves the window 100 segments back and shows the start
  of the part it loaded (before, the view stayed where it was, so nothing
  seemed to happen; bug, 2026-11-13). The two are outlined buttons like
  "Hide text", at the grid's left edge, "↑ Show earlier text" above it and
  "↓ Jump to the latest text" below; each stays in place and is disabled when
  it has nothing to do (nothing earlier; the newest text already followed);
  filled blue is kept for primary actions. Reading on and
  reaching the end of the rows shown loads the next 100 by itself, up to the
  newest text, where following resumes (owner, 2026-11-13: a "Show later
  text" button did in practice what "Jump to the latest text" does, and was
  removed). The DOM never holds more than one window. The
  newest row is the one the latest event changed (the last segment of a
  recognition pass, correction block or translation block), so during
  correction and translation newer rows follow it in the window; the page
  puts that row at the bottom of the text box. Scroll positions are computed
  from the rows' on-screen rectangles in `follow.js` (tested in Node,
  `tests/js/follow.test.mjs`): `offsetTop` is measured from the page, not
  the box, and in the one-column layout it scrolled the newest row out of
  view (bug, 2026-11-13).
- **Plain words only.** Nothing the core says is shown as it is: a known
  kind of message (subtitles written, burn-in started, a fallback engine)
  becomes one sentence in the interface language; every core line (and each
  step's own description) is listed, collapsed, under "Technical details".
  The failed screen names the kind of failure in words and keeps the core's
  line there too. Yellow is kept for the one real warning: phrases left
  untranslated or uncorrected, with the minutes of those phrases ("Some
  phrases were not translated (10:21–10:30): the subtitles there are in
  English. The model's answer for them failed the automatic check twice, so
  they were kept as they were. Running the video again would most likely give
  the same result."), on the waiting screen as soon as the block is done and
  on the Done screen (also when opened from the recent results). The warning
  suggests nothing that costs money: a part is left as it was only after two
  rejected answers to the same request, which a re-run would most likely
  repeat; a transient failure (network, rate limit, timeout) is retried by the
  API client and otherwise stops the whole run, with its own message on the
  failed screen (owner's bug report, 2026-11-13; before, the warning said to
  run the video again).
- **Reload.** A reload during a run reattaches: the steps are rebuilt from the
  replayed events and the text from the current window.
- **Narrow windows** stack the steps above the text (below 56 rem) and the two
  text columns into one (below 40 rem).

## The result

The Done screen has a player: an HTML5 `<video>` with two subtitle tracks
(the target language on, English off), served as WebVTT converted from the
result's `.srt` on request (no extra pipeline output). Under the video, the
page's own buttons choose the subtitles (target language / English / off;
the choice is stated in words beside them). The browser's own subtitle menu
(Firefox-family browsers have one too: Off / Русский / English; an earlier
note here said they had none, which was wrong) changes the same tracks, and
the buttons follow it: they listen to the tracks' `change` event, so the page
and the menu always agree (bug, 2026-11-13: the buttons said "off" while the
menu had English on). While the video and the tracks are still loading, the
browser changes the modes by itself (automatic track selection, resets); such
a change is not the reader's, so the page puts its choice back, and follows
the menu only once everything has loaded (`subs.js`, tested in Node; the
owner's second check, 2026-11-13: a video with the target track opened with
"Off" selected). The default is always the target language; the page
remembers no earlier choice. It plays the video without burned-in subtitles when
there is one (the downloaded original `<stem>.mp4`, then the subtitle-track
`<stem>.subs.mp4`). With only the burned-in `<stem>.<lang>.mp4` the
target-language track is not offered at all, in the buttons or in the
browser's menu (showing it drew a second copy over the picture): English and
Off only, starting off, with one line saying the translation is already in
the picture. Tried in Firefox 159 and Edge (Chromium), headless, on the
owner's burned-in result and on the replay demo, choosing from the buttons and
from the menu (a script sets a track's mode, as the menu does). A file
input's original video lives outside the output folder and is not served, so a
file-mode result plays one of the produced MP4s. An audio-only input is linked
(or copied) into its result folder and gets an `<audio>` player with its text
beside it, following the audio (a click on a line seeks there).

The file list says what each file is for, by its name (`web/labels.py`):
"Subtitles (Russian) — for the TV and VLC", "Subtitles (English)", "Video with
the Russian subtitles burned in — for the TV", and so on, with the file name
below. New YouTube result folders and files are named after the title cut at
a word (`youtube.safe_name(words=True)`); a folder or a download made before
keeps its old name, so a re-run reuses it.

The start page lists the last finished results (title, date, target language)
with "Watch" and "Open folder": a read-only scan of the folders directly inside
the output root that hold `talk-autopsy-result.json` (written by every
finished web job) or `done.json` (CLI YouTube runs); at most 300 folders
scanned, 12 listed. After a reload the last result is reachable from there.

**Estimate from the cache.** The estimate screen counts only what will run
(`talk_autopsy/rerun_plan.py`, no API call): steps whose cache stamps match
are listed as "already done, taken from the cache" and cost nothing; for the
correction and the translation only the blocks that would be sent count; MP4s
that would be reused take no time; the price of a run from scratch is shown
beside it. When the correction has to run, the translation counts in full and
the amount says "up to" with the reason (which translation blocks change is
known only after the correction). The over-limit confirmation uses this
amount; the pipeline's own check of the full Claude estimate gets twice the
full estimate as its ceiling, so a cheap re-run of a long video is not
stopped. A fully cached re-run of a 25:56 video: estimate 0.00 USD, run 4.8 s
(bug, 2026-11-13: 0.55 USD shown, eight minutes of re-encoding).

**Outputs from cache.** A re-run makes the MP4s again only when something
they are made from changed: the source video, the subtitles (with their
style), the encoder settings or ffmpeg itself; a deleted, truncated or
replaced file is made again too (`outputs` stamps,
`.talk-autopsy/<file>.stamp.json`). Subtitle copies are left alone when
their content is the same. When every output was reused, the "Files" step
says "from cache" (bug, 2026-11-13: a fully cached re-run burned the
subtitles in again for minutes).

**Service files out of sight.** `meta.json` (written by the download),
`done.json` (CLI YouTube runs and the page) and `talk-autopsy-result.json`
live in a hidden service folder inside the result folder, `.talk-autopsy/`
(dot name, plus the Windows hidden attribute), so the result folder shows
only videos and subtitles. Readers (recent results, the player, the
playlist's "already done" check) look there first, then at the top level,
where folders made before 1.1 have them; the next marker written into such a
folder moves its old service files in. No frozen module reads these files:
the pipeline's own `meta.json` is in `work/<video-id>/`, so no cache key
depends on them.

**Money, one way everywhere.** Every amount on the page (the estimate, each
paid step on the waiting screen, the running total, Done, Failed, a result
opened from the start page) is written by one function, `static/money.js`,
tested per language in Node: the total first, then each service, two
decimals, the decimal comma in Russian and Lithuanian, "< 0.01" for an
amount above zero but below one cent, and "(from cache)" after a part that
cost nothing because its steps were reused:

    Spent: 0.40 USD — Claude 0.40 + speech recognition 0.00 (from cache)
    Потрачено: 0,40 USD — на Claude 0,40 + на распознавание речи 0,00 (из кэша)

A step shows only its own service's part ("Spent: 0.04 USD — Claude 0.04");
the estimate shows the line without "Spent" (its label says what it is), "up
to …" when it is an upper bound. The console window's finish line follows
the same rule in English (`web/money.py`).

**A service that is down.** A Claude request that fails on the service's
side (HTTP 503 unavailable, 529 overloaded, 429 rate limited, a timeout, a
dropped connection) is tried again after 5, 15 and 30 s before the run stops
(`pipeline-v3.5`, `core/transient.py`; owner's report, 2026-11-13: a
34-minute video stopped on one 503 after the SDK's two retries within five
seconds). If it still fails, the Failed screen says in words which service was
unavailable and that it is on the service's side, that the finished steps are
kept, and what was spent before the stop (finished steps plus the paid answers
of the step that stopped, from `structured_call.spend_observer`). "Run again"
asks for the estimate of the same job first, as usual; the cache makes it
continue where it stopped. The exception's class and message are only under
"Technical details", for every failure. AssemblyAI is not retried: its
failures never stop a run (the first pass falls back to another engine, the
key-term pass keeps the first pass).

**Run log.** Every finished or failed web run appends a line to the result
folder's `.talk-autopsy/runs.jsonl` (state and money). When a run finishes
after failed attempts (since the previous finished run of that video), Done
shows this run's money and, on a second line, the video's total with them;
the console's finish line says it too, and the result marker keeps it for the
start page.

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
| `GET /api/jobs/{id}/transcript?start=&count=&follow=` | a window of rows and the summary (counts, newest position); `rows=false`: the summary only (text panel collapsed) |
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
`target_lang` from the offered list. A playlist link is refused by
`/api/estimate` with the code `playlist` (the page opens the course screen
instead); courses have their own requests (Courses, above): the same `profile`,
`domain` and `target_lang`, plus `url` (the playlist), `selected` (playlist
positions), `course_glossary` and the confirmation of the total.

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
A playlist link (`list=` without `v=`) gives a synthetic course of six
invented lessons, one of them private; each lesson plays the recording into
its lesson folder.
Committed recordings (an invented talk, no real text):
`tests/fixtures/replay/synthetic-talk.jsonl` (finished),
`synthetic-talk-failed.jsonl` (fails in translation) and
`synthetic-talk-untranslated.jsonl` (finished, its first translation block
left in English); regenerate with
`uv run python -m tests.make_replay_fixture`.

## Screenshots

`docs/img/web-*.png` (README) are made from synthetic data only:

```
uv run python scripts/web_screenshots.py                    # English -> docs/img/
uv run python scripts/web_screenshots.py --ui lt --out work/screenshots-lt
uv run python scripts/web_screenshots.py --ui ru --set all --out work/screenshots-ru   # + warning, collapsed
uv run python scripts/web_screenshots.py --set course          # only the four course pictures
```

The README set includes the course screens (`web-course*.png`): the replay's
invented demo course of six lessons (one private), the course header while
lesson 2 runs, the course Done screen and a lesson with "previous / next
lesson". The course folder is a temporary local path, so the course screen's
picture shows only its last part.

The script needs Edge or Chrome (driven headless over the DevTools protocol,
with the `websockets` package already in the environment) and ffmpeg. It runs
the replay server in-process on the committed recordings, in a temporary
folder of its own (so the recent-results list holds only the replayed demo),
uses link mode on the form (no local folder on screen), plays a title card it
makes with ffmpeg, and pauses the playback right after chosen events to catch
the waiting screen: during the second recognition pass (a spinner) and after
the first translation block (a real bar, corrections in place). It opens the
text panel for these pictures (collapsed by default); `--set collapsed` adds
the waiting screen as it opens, and checks that no row was built. Each picture
is cut at the bottom of its card; the Done picture ends at the cost line, and
the script checks that the result folder (a local path) is below it. About
four minutes per language (the replay runs at natural speed so the elapsed
time matches the step times).

Without a browser the same screens can be taken by hand: `uv run talk-autopsy
web --replay tests/fixtures/replay/synthetic-talk.jsonl --speed 1`, open the
page with `?ui=en`, use a link (one containing `long` shows the over-limit
confirmation), and for the failed screen replay
`synthetic-talk-unavailable.jsonl` (the Claude API answers HTTP 503 to every
try: the README picture, with "Run again") or `synthetic-talk-failed.jsonl`
(an unexpected error). The Done screen then plays a test pattern and
shows the demo folder's path below the player: keep it out of the picture.

## Not in v1.2

No cancel / stop button inside a video (the core has no cooperative
cancellation; a course can stop after the current lesson); target languages
other than Russian work but their quality is not measured; the Lithuanian
interface strings are not yet checked by a native speaker
(`work/lt-strings-review.md`, local).
