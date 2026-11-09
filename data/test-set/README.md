# Test set

Six fragments of English technical speech, 5–10 minutes each. Every stage of the
pipeline is measured on the same fragments, so results stay comparable from one
experiment to the next. T6 is a sealed hold-out.

| ID | Type | Speech | Duration | Topic | Glossary terms |
|---|---|---|---|---|---|
| T1 | conference talk with live demo | native speaker (to be confirmed) | 9:27 | AI coding agents | TBD |
| T2 | conference talk, slides | strong regional accent | 10:00 (cut from 27:52) | AI coding agents, terminal tooling | TBD |
| T3 | conference talk | other non-native accent | TBD | TBD | TBD |
| T4 | screen demo / tutorial | fast speech, code identifiers, shell commands | TBD | TBD | TBD |
| T5 | university lecture | slow pace, long sentences | TBD | TBD | TBD |
| T6 | sealed hold-out (another accent) | — | TBD | — | TBD |

## What each fragment tests

- **T1:** the baseline case: clear speech, technical vocabulary and a live demo
  with names of tools and commands. If the pipeline fails here, it fails
  everywhere.
- **T2:** a strong accent on technical terms, the case where plain Whisper plus
  machine translation broke down. The stage 1 gate (WER ≤ 15%) is measured on
  this fragment.
- **T3:** a different non-native accent, so that improvements on T2 are not
  tuned to one accent only.
- **T4:** fast speech full of code identifiers and shell commands: tests whether
  correction keeps them verbatim and translation leaves them untranslated.
- **T5:** slow speech with long sentences: tests segment boundaries, block
  overlap in translation and subtitle reading speed.
- **T6:** sealed hold-out with another accent. It is not listened to, not
  transcribed for prompt work and not used during development; it runs once, at
  the stage 2 gate, with the prompts already frozen.

T3–T6 are not chosen yet; they will be selected deliberately to cover the cases
above, not taken from whatever files are at hand.

## File naming

| File | Content | In the repository |
|---|---|---|
| `T<n>.source.mp4` | the original video or audio | no |
| `T<n>.wav` | 16 kHz mono audio of the fragment (`talk-autopsy cut`) | no |
| `T<n>.draft.txt` | speech recognition draft, `[mm:ss] text` per line, proofread while listening (`talk-autopsy draft`) | no |
| `T<n>.ref.txt` | reference transcript, checked by hand (`talk-autopsy ref-from-draft`) | no |
| `T<n>.glossary.md` | terms of the fragment, in the format of `talk_autopsy/glossary.py` | yes |

Reference transcripts and media are not published, so the benchmark is
reproducible in method but not byte for byte.
