# Test set

Six planned fragments of English technical speech, 5–10 minutes each, plus T7
(v1.2, 18 minutes). T1 and T2 are scored; T6, the hold-out, was run once
(2026-11-11); T3–T5 are not selected; T7 is the Web3 tutorial fragment of v1.2
(2026-11-14). Every stage of the pipeline is measured on the same
fragments, so results stay comparable from one experiment to the next.

| ID | Type | Speech | Duration | Topic | Glossary terms |
|---|---|---|---|---|---|
| T1 | conference talk with live demo | native speaker (to be confirmed) | 9:27 | AI coding agents | 37 (reviewed against the reference) |
| T2 | conference talk, slides | non-native speaker | 10:00 (cut from 27:52) | AI coding agents, terminal tooling | 39 (reviewed against the reference) |
| T3 | conference talk | another non-native speaker | TBD | TBD | TBD |
| T4 | screen demo / tutorial | fast speech, code identifiers, shell commands | TBD | TBD | TBD |
| T5 | university lecture | slow pace, long sentences | TBD | TBD | TBD |
| T6 | workshop with live demo (hold-out, run once) | non-native speaker | 10:00 (cut 25:46–35:46 from 41:24; speech share 93.8%) | AI-assisted software development | 51 (drafted by Gemini from the reference; tracked since the run) |
| T7 | Web3 tutorial with code on screen (v1.2) | not determined | 18:00 (cut 21:49:15–22:07:15 from 38:10:24) | Solidity and Foundry | 79 (drafted by Gemini from the reference, `ref-glossary-v2`, one of six categories each) |

## Sources

The fragments are cut from public talks, lectures and interviews. By the
owner's decision of 2026-11-12 the sources are named openly, so that the
benchmark can be rebuilt from the same recordings. Our local copies are
reposts from a social network, trimmed differently from the originals; the
reposts are not cited. Where the original public recording was identified, it
is linked below; where it was not, the event or topic is named without a link.
Cut points are given on **our copy's** timeline, which can be offset from the
original's. Speakers are described only as native or non-native speakers. The
media and the reference transcripts are not published. Links checked on
2026-11-12 (YouTube metadata via yt-dlp, no download).

| ID | Source | Speaker(s) | Original recording | Our copy and the cut |
|---|---|---|---|---|
| T1 | Code with Claude 2025 keynote (Anthropic, San Francisco, 2025-05-22), segment on Claude Code | Cat Wu; native speaker (to be confirmed) | official recording not identified | a ~9.5-minute repost clip (9:27) from a social network; the whole clip is the fragment; its position in the keynote is not known |
| T2 | "Mastering Claude Code in 30 minutes", Code with Claude 2025 (channel Anthropic, 2025-05-22) | Boris Cherny; non-native speaker | <https://www.youtube.com/watch?v=6eBSHbLKuN0> (28:07) | a repost, 15 s shorter (27:52 vs 28:07); fragment 0:00–10:00 of our copy |
| T6 | a workshop on AI-assisted software development: an "agent skills" package, with a live demo in a code editor | two presenters, not identified; non-native speaker plus a second presenter | official recording not identified | a 41-minute repost (41:24); fragment cut blind at 25:46–35:46 of our copy (below) |
| real-01 | "Stanford CS153 Frontier Systems \| Scale, AGI, and the Future of Everything" (channel Stanford Online, 2026-06-15) | Sam Altman (guest) and the course host | <https://www.youtube.com/watch?v=F_7M4Hc-usM> (41:10) | a shortened repost (38:30 vs 41:10); the whole copy; operational run, not scored |
| T7 | "Learn Solidity Smart Contract Development \| Full 2024 Cyfrin Updraft Course" (channel Patrick Collins, 2024-07-31) | not determined | <https://www.youtube.com/watch?v=-1GB6m39-rM> (38:10:24) | not a repost: only the window was fetched from the original (below); fragment 21:49:15–22:07:15 of the original's timeline |
| stress-01 | "Andrej Karpathy: From Vibe Coding to Agentic Engineering w/ Stephanie Zhan" (channel Sequoia Capital, AI Ascent 2026, 2026-04-29) | Andrej Karpathy, Stephanie Zhan; two speakers, fast conversational speech | <https://www.youtube.com/watch?v=96jN2OCOfLs> (29:49) | the same length (29:50); the whole copy; operational run, not scored |

The names above are the only speaker names in the repository: this section is
the one place the de-identification check (`scripts/check_names.py`) allows
them.

## What each fragment tests

- **T1:** the baseline case: clear speech, technical vocabulary and a live demo
  with names of tools and commands. If the pipeline fails here, it fails
  everywhere.
- **T2:** a non-native speaker saying technical terms, the case where plain Whisper plus
  machine translation broke down. The stage 1 gate (WER ≤ 15%) is measured on
  this fragment.
- **T3:** a different non-native speaker, so that improvements on T2 are not
  tuned to one speaker only.
- **T4:** fast speech full of code identifiers and shell commands: tests whether
  correction keeps them verbatim and translation leaves them untranslated.
- **T5:** slow speech with long sentences: tests segment boundaries, block
  overlap in translation and subtitle reading speed.
- **T6:** hold-out with another non-native speaker. The owner chose the source from
  candidates by type (non-native speaker, technical workshop); its topic,
  AI-assisted software development, is close to T1 and T2 and is known. The
  10-minute window was cut blind (below). Nobody has watched or listened to
  it. After the `pipeline-v2` freeze three engines transcribed it to build the
  reference, a blind Gemini audio judge decided the disputed spans, and Gemini
  extracted the glossary; **no human has checked the reference** (no fluent
  listener available). No prompt or setting was tuned on it; the prompts'
  examples were only checked by code not to occur in any test-set glossary or
  reference, T6's included. It ran once, with `pipeline-v3`,
  on 2026-11-11 (`docs/experiments.md`, T6 protocol and T6 result).

- **T7 (v1.2):** a Solidity / Foundry tutorial with code and a terminal on
  screen, the kind of video the tool is mainly used on: identifiers, CLI
  commands (`forge`, `cast`, `anvil`), version numbers and Web3 jargon in fast
  speech. It measures the frozen pipeline on this domain before any Web3
  dictionary v2 is built (`docs/experiments.md`, T7 baseline). Nobody had
  watched it when it was chosen.

T3–T5 are not chosen yet; they will be selected deliberately to cover the cases
above, not taken from whatever files are at hand.

**How T6 was cut (blind, 2026-11-09).** Nobody listened to or transcribed the
file. The 10-minute window was chosen by speech density alone: ffmpeg
`silencedetect` (−35 dB, at least 0.5 s) on the whole audio, the first 5 and
the last 3 minutes skipped, and the window (1-second steps) with the largest
share of non-silent audio taken, the earliest on a tie. Result: 25:46–35:46,
93.8% non-silent (the whole file: 92.5%, 245 silences). Its content stays out
of everything outside `data/test-set/` and `work/T6/`; only counts are recorded
(`docs/experiments.md`, T6 protocol).

**How T7 was chosen and cut (2026-11-14).** Six candidates from six channels
(English, public, 2024 or later, Solidity / Foundry with code or a terminal on
screen; videos already processed with the tool and the sources above
excluded) were ranked by a neutral density estimate on YouTube's automatic
captions: regex counts of identifiers, CLI commands, version numbers, `0x`
addresses, Solidity keywords and words rare in general English, over 18-minute
windows in 15-second steps; no Web3 dictionary was used. The owner chose this
course and the window with the most distinct technical tokens (87 by that
estimate; 68 without Solidity keywords) and confirmed that he had not watched
the course. Only the window was fetched, not the 38-hour video: `yt-dlp -f 234
--download-sections "*21:49:00-22:07:30"` (the HLS audio stream, 15 s of margin
on each side; a first attempt on the direct m4a stream stalled and was
stopped), then cut exactly with `talk-autopsy cut T7.source.mp4 --start 15
--end 1095` to 16 kHz mono (1080.0 s). Speaker: not determined (nobody has
listened to it).

## Reference conventions

How the reference transcripts are written. The rules marked *in code* are also
applied by the metrics, so a hypothesis is not penalized for what the convention
leaves out.

| Case | Rule |
|---|---|
| Slash commands | written as typed: `/theme`, `/terminal-setup`, `/install-github-app` |
| GitHub mention | `@claude` |
| Fillers `uh`, `um`, `er`, `ah`, `hmm` | not written. *In code:* removed from every text by the metrics normalizer (`metrics.tokenize`, `metrics.FILLERS`) before scoring and before the three-engine alignment, so they never count as WER errors or disagreements |
| `you know`, `like`, `so`, `I mean` | written: they are words |
| Plurals of glossary terms | the glossary keeps the base form. *In code:* term matching also accepts the regular English plural (`-s`, `-es`, `y` -> `ies`) of the last word of a term (`diffs`, `pull requests`, `libraries`) |
| Numbers | digits |
| Thousands separators | not written: `3000` (added 2026-11-10 after the judge calibration). *In code:* removed by `judge.normalize_for_adjudication` |
| Immediate repetitions and restarts | written once: "the the" -> "the"; "we can we can" -> "we can" (added 2026-11-10 after the judge calibration). *In code:* `judge.normalize_for_adjudication` collapses an immediately repeated sequence of 1-4 words on both sides of a judge comparison and in a reference built from a judged file; grammatical doubles ("that that", "very, very") are collapsed too, which, applied to both sides, cannot create a disagreement; a repetition split by a span boundary is not seen. The metrics normalizer is unchanged. Rows added after the judge calibration are not passed to the judge's prompt (`audio-judge-v1` stays as calibrated); the code applies them |
| Key combinations | `Shift+Enter`, `Ctrl+C`, `Cmd+F` (added 2026-11-10 after the judge calibration). *In code:* the same function canonicalises `shift enter`, `Shift-Enter`, `shift+enter` (and Ctrl/Alt/Cmd with Tab/Esc/Enter or a letter joined by + or -) |
| Hallucinated lines, segments with no word (only `.`) | removed. *In code:* the speech recognition engines drop wordless segments before writing a transcript; ids are assigned after dropping |

## File naming

| File | Content | In the repository |
|---|---|---|
| `T<n>.source.mp4` | the original video or audio (T7: only the window plus 15 s on each side, audio only) | no |
| `T<n>.wav` | 16 kHz mono audio of the fragment (`talk-autopsy cut`) | no |
| `T<n>.draft.txt` | speech recognition draft of 2026-11-09; superseded by the adjudication, not proofread | no |
| `T<n>.adjudication.md` | the spans where the three engines disagree, with clips, for the owner to decide (`talk-autopsy adjudicate`) | no |
| `T<n>.ref.txt` | reference transcript: agreed words plus the owner's decisions (`talk-autopsy resolve`) | no |
| `T<n>.ref.protocol.json` | where every word of the reference came from: consensus, an engine's variant, or typed (`talk-autopsy resolve`) | no |
| `T<n>.glossary.md` | terms of the fragment, in the format of `talk_autopsy/glossary.py` | yes |

Reference transcripts and media are not published, so the benchmark is
reproducible in method but not byte for byte.
