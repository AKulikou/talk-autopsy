# Test set

Six planned fragments of English technical speech, 5–10 minutes each. T1 and
T2 are scored; T6, the hold-out, was run once (2026-11-11); T3–T5
are not selected. Every stage of the pipeline is measured on the same
fragments, so results stay comparable from one experiment to the next.

| ID | Type | Speech | Duration | Topic | Glossary terms |
|---|---|---|---|---|---|
| T1 | conference talk with live demo | native speaker (to be confirmed) | 9:27 | AI coding agents | 37 (reviewed against the reference) |
| T2 | conference talk, slides | non-native speaker | 10:00 (cut from 27:52) | AI coding agents, terminal tooling | 39 (reviewed against the reference) |
| T3 | conference talk | another non-native speaker | TBD | TBD | TBD |
| T4 | screen demo / tutorial | fast speech, code identifiers, shell commands | TBD | TBD | TBD |
| T5 | university lecture | slow pace, long sentences | TBD | TBD | TBD |
| T6 | workshop with live demo (hold-out, run once) | non-native speaker | 10:00 (cut 25:46–35:46 from 41:24; speech share 93.8%) | AI-assisted software development | 51 (drafted by Gemini from the reference; tracked since the run) |

## Sources

The fragments are cut from public talks, lectures and interviews. By the
owner's decision of 2026-11-12 the sources are named openly, with links and
the cut points, so that the benchmark can be rebuilt from the same audio; the
list is added in a separate commit once the owner has checked it. Speakers
are described only as native or non-native speakers. The media and the
reference transcripts are not published.

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
| `T<n>.source.mp4` | the original video or audio | no |
| `T<n>.wav` | 16 kHz mono audio of the fragment (`talk-autopsy cut`) | no |
| `T<n>.draft.txt` | speech recognition draft of 2026-11-09; superseded by the adjudication, not proofread | no |
| `T<n>.adjudication.md` | the spans where the three engines disagree, with clips, for the owner to decide (`talk-autopsy adjudicate`) | no |
| `T<n>.ref.txt` | reference transcript: agreed words plus the owner's decisions (`talk-autopsy resolve`) | no |
| `T<n>.ref.protocol.json` | where every word of the reference came from: consensus, an engine's variant, or typed (`talk-autopsy resolve`) | no |
| `T<n>.glossary.md` | terms of the fragment, in the format of `talk_autopsy/glossary.py` | yes |

Reference transcripts and media are not published, so the benchmark is
reproducible in method but not byte for byte.
