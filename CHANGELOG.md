# Changelog

## 1.1.0 — DATE-TBD (set when the `v1.1` tag is created)

A local web page over the same core, on the frozen pipeline (`pipeline-v3.3`:
model requests identical to `pipeline-v3`, plus two bug fixes in how
translation answers are checked and cached; every cached T2 and T6 answer gets
the same verdict as before, so 1.0's measured numbers carry over without
re-runs).

**Web UI**

- `talk-autopsy web` (desktop shortcut "Talk Autopsy" on Windows): a page on
  this computer only (127.0.0.1) that runs one video end to end: a YouTube
  link or a file on this computer (native file dialog), output profile (TV,
  laptop, everything), Web3 glossary, target language. Interface in English,
  Russian and Lithuanian. Design and security model: `docs/web.md`.
- Cost and time estimate before anything is spent, and an explicit
  confirmation above 3 USD per video. The time estimate comes from this
  machine's own history, with measured built-in values on a first run; it
  never blocks a run.
- The waiting screen shows the steps (with a real position where the core
  reports one: download bytes, correction and translation blocks, burn-in
  seconds; elsewhere a spinner and the elapsed time), the running cost and
  tokens, and the text as it becomes ready, in two aligned columns: English
  (the recognised text, then each correction block's changes in place:
  removed words struck through, added words underlined) and the translation,
  filled block by block. It follows the newest text until you scroll up
  ("jump to the latest text" brings it back), keeps at most one window of rows
  in the page, and a reload rebuilds it.
- In-browser player on the Done screen: the video without burned-in
  subtitles when there is one, with the target-language and English subtitles
  as switchable tracks; audio-only inputs get an audio player with the text
  beside it. The start page lists recent results with "Watch" and "Open
  folder".
- Desktop shortcuts: "Talk Autopsy" starts the page (closing its window stops
  it; a second start opens the running page), "Talk Autopsy (console)" keeps
  the console launcher for playlists.
- Every job's events are recorded locally; `talk-autopsy web --replay <file>`
  plays a recording back with no pipeline, network or cost (synthetic
  recordings in `tests/fixtures/replay/`).
- `doctor` reports whether the web UI can start; `fastapi` and `uvicorn` are
  regular dependencies.

**Changed**

- **One per-video cost limit.** `run --max-cost` (default 3 USD) is now
  compared with the whole estimate, Claude plus speech recognition, as the web
  page does; before, the CLI compared the Claude part only. A video of about
  2.3 to 3.7 hours, which passed before, now stops before any work (and before
  the download) unless `--max-cost` is raised. Playlists apply the same
  per-video limit. One function (`youtube.over_video_limit`) serves the CLI
  and the page.
- The waiting screen's text panel (English and translation side by side) is
  collapsed by default (owner's decision): the steps take one column, and a
  summary line (corrections made, phrases translated) and the warnings stay
  visible. "Show text as it is processed" opens it; the browser remembers the
  choice.
- One way to the newest text on the waiting screen: "Jump to the latest text"
  stays, "Show later text" is gone; reading earlier text and reaching the
  end of the rows loads the next ones by itself.
- The page's console window shows only the address, one line per run start
  and finish (ok, or with problems: see the log) and real errors; warnings
  and tracebacks go to the log file only.

**Fixed**

- One segment that failed a check no longer costs its whole block
  (`pipeline-v3.4`): the block is used and only that segment keeps its
  recognized text (correction) or stays in English (translation); the warning
  names its minutes. Found on a 26-minute talk where five minutes kept the
  recognized text because "run MCP" was corrected to "run /mcp", which the
  check took for removing the term MCP; a term written as a command is no
  longer a removal. Such a result is not cached as finished: the next run
  sends only the blocks that need it and reuses every other block.
- A translation block was rejected, and its five minutes left in English, when
  one segment had nothing to translate (a bare version number such as
  `0.8.26.`): numbers, identifiers, file names and URLs no longer count as
  English words in the "left in English" check (`pipeline-v3.3`). Ordinary
  English sentences are still rejected.
- A translation or correction with a failed block was cached as finished, so
  running the video again never retried it. Now the next run sends only the
  failed translation blocks (the accepted ones are reused, nothing is billed
  twice) and redoes a correction with a failed block. Existing finished
  results stay cached.
- Found in the owner's first real run of the page: a part left untranslated
  is now a plain warning with its minutes on the waiting and Done screens
  ("Some phrases were not translated (10:21–10:30): the subtitles there are in
  English..."), not a counter of "blocks"; it no longer advises running the
  video again, which after two rejected answers would most likely fail the
  same way and cost money again; the
  core's English lines are no longer shown on a Russian page (known messages
  become a sentence in the interface language, the rest goes under "Technical
  details"); buttons under the player choose the subtitles (target language,
  English, off), kept in step with the browser's own subtitle menu in both
  directions; a video with the translation burned in offers only English and
  off (its translation track drew a second copy over the picture); the
  browser's own track changes while the video loads no longer switch the
  default (the target language) off; the file
  list says what each file is for ("Subtitles (Russian) — for the TV and
  VLC"); new file and folder names are cut at a word, not mid-word (older
  folders keep their names); `meta.json`, `done.json` and the page's marker
  moved into a hidden `.talk-autopsy` folder inside the result folder.
- Found in the owner's second check: during correction and translation the
  waiting screen's text did not follow the newest change, and "Jump to the
  latest text" did not reach it, whenever the text box was small or low on
  the page (one-column layout, 150 % zoom); the scroll position was measured
  from the page instead of the box.

**Pipeline (observability and cleanup only)**

- `pipeline-v3.1`: the pipeline's events carry numbers (estimates, cost,
  seconds and tokens of each step) and, for every finished correction or
  translation block, its text. Golden tests show the model requests and
  outputs are byte-identical to `pipeline-v3`; a failing progress display
  never stops a run. Real positions for the YouTube download (bytes) and the
  burn-in (seconds of video encoded).
- `pipeline-v3.2`: the unused `edge-tts` dependency and the `ingest.download`
  stub removed; cache keys, model requests and outputs unchanged.
  `metrics.timing_drift` stays: removing it would invalidate cached runs.

**Code**

- `cli.py` split into the package `talk_autopsy/cli/` (one module per command
  group); help, dry-run and doctor output unchanged, checked by snapshot tests.

**Not in 1.1**

- A stop button (the core has no cooperative cancellation); playlists in the
  page (they stay with the CLI and the console launcher).
- Measured quality for target languages other than Russian.
- Voice-over / dubbed audio (stage 4).

## 1.0.0 — 2026-11-12

First release: the CLI tool of stage 3, on the frozen pipeline `pipeline-v3`.

**What it does**

- Takes a local video or audio file, or a YouTube link, and makes Russian
  subtitles (`.srt` and `.ass`; the target language is a parameter), with an
  English subtitle file alongside.
- Optionally writes a TV-ready MP4 with the subtitles burned in
  (`--output mp4-burned` or `all`).
- Drafts a glossary of the talk's terms automatically and merges it with a
  domain glossary of your own (`glossaries/`, for example `web3.md`).
- Windows launcher (`scripts\windows\Talk Autopsy.bat`): asks in Russian for a
  link or a file, shows the cost estimate, writes the TV file and subtitles.
- Re-runs from cache take seconds and cost nothing; every step shows its
  estimate before it spends.

**Evaluation in five lines** (details: [README › Results](README.md#results),
[Known limitations](README.md#known-limitations))

1. Term recognition on T2 (the development fragment, three runs): 94.4–96.3%
   term accuracy against 80.4% for AssemblyAI alone and 61.7% for raw Whisper.
2. One Gemini call reaches 93.5% on the same audio: on terms the pipeline is
   not shown to be better than it (the lead is within the run-to-run spread).
3. Hold-out T6, run once: 72 of 72 term occurrences, lower bound 91.7%;
   verdict "pass (not demonstrated)".
4. Translation is not better: one viewer rated 12 random T6 sentences 4.42 for
   Talk Autopsy, 4.42 for Whisper + Google Translate, 4.33 for plain Gemini.
5. Limits: one topic (AI-assisted software development), two scored fragments
   plus one hold-out, references never proofread by a fluent listener;
   1.1–1.3 USD and about 10 minutes per hour of video at list prices.

**Not in 1.0**

- Web interface (v1.1).
- Voice-over / dubbed audio (stage 4).
- Fragment T3 from another domain, so transfer beyond one topic is unmeasured.
- The git-history rewrite before the repository is made public (v1.1).
