# Changelog

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
