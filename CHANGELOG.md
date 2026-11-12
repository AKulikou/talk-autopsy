# Changelog

## Unreleased

**Web UI preview (v1.1, session 1 of 3)**

- `talk-autopsy web`: a local page (127.0.0.1 only) that runs one video end to
  end: a YouTube link or a file on this computer (native file dialog), output
  profile (TV, laptop, everything), Web3 glossary, target language; cost and
  time estimate first, an explicit confirmation above 3 USD per video; steps,
  running cost and the text of each finished step as Server-Sent Events; the
  produced files and "Open folder" at the end. Interface in Lithuanian, Russian
  and English. Design and security model: `docs/web.md`.
- Time estimate from this machine's own history (user cache directory), with
  measured built-in values on a first run; it never blocks a run.
- `doctor` reports whether the web UI can start; `fastapi` and `uvicorn` are
  regular dependencies.
- Not yet: the side-by-side live view, an in-browser player, a desktop shortcut
  for the page, playlists in the page, cancelling a run.

**Progress events (`pipeline-v3.1`, session 2)**

- The pipeline's events carry numbers (estimates, cost, seconds and tokens of
  each step) and, for every finished correction or translation block, its
  text. Observability only: golden tests show the model requests and outputs
  are byte-identical to `pipeline-v3`; a failing progress display never stops
  a run.
- Real positions for the YouTube download (bytes) and the burn-in (seconds of
  video encoded).

**Web UI (v1.1, session 2 of 3)**

- The waiting screen shows the text as it becomes ready, in two aligned
  columns: English (the recognised text, then each correction block's changes
  in place: removed words struck through, added words underlined) and the
  translation, filled block by block. It follows the newest text until you
  scroll up ("jump to the latest text" brings it back), keeps at most one
  window of rows in the page (an hour-long talk stays smooth), and a reload
  rebuilds it. Tokens per step are shown.
- In-browser player on the Done screen: the video without burned-in
  subtitles when there is one, with the target-language and English subtitles
  as switchable tracks; audio-only inputs get an audio player with the text
  beside it.
- The start page lists recent results with "Watch" and "Open folder".
- Desktop shortcuts: "Talk Autopsy" starts the page (closing its window stops
  it; a second start opens the running page), "Talk Autopsy (console)" keeps
  the console launcher for playlists.
- Every job's events are recorded locally; `talk-autopsy web --replay <file>`
  plays a recording back with no pipeline, network or cost (synthetic
  recordings in `tests/fixtures/replay/`).
- An unexpected error is named in the interface language; harmless "connection
  reset" tracebacks no longer appear in the console; page assets are
  revalidated after an update.

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
