# Decision log

Decisions that shape the project: pipeline, scope, test set, process. Newest
first. Experiments and their numbers go to [experiments.md](experiments.md); this
log records what was decided and why.

Entry format: date, decision, why, and what it rules out.

## 2026-11-09 — reference transcripts by three-engine consensus, not proofreading

The owner is not a native English speaker, so a reference proofread by ear from
one engine's draft would carry the owner's own errors into every metric. The
reference is built instead from what three independent engines agree on
(faster-whisper large-v3 locally, AssemblyAI Universal-3.5 Pro, Gemini), and
the owner decides only the spans where they disagree, each with a short clip and
the competing variants. Sending the test-set audio to AssemblyAI and Google for
this was approved by the owner. The drafts from the first Whisper run are
superseded and not proofread.

What it rules out: claiming that AssemblyAI or Gemini scores are unbiased. Both
helped build the reference, so their WER against it is biased in their favour;
the protocol (how often each engine's variant was chosen in the disputed spans)
is the fairer comparison for them. Where two engines share the same error
(on T2 both faster-whisper and AssemblyAI heard "Quadcode" for "Claude Code"),
the majority pre-mark is wrong and only the owner's listening catches it.

## 2026-11-09 — T1 and T2 from the files at hand; T3–T6 selected deliberately

T1 (a conference talk with a live demo, 9:27) and T2 (a talk with a strong regional accent, first 10:00 of 27:52) come from videos already on this machine, because
they cover the two first cases: clear speech with technical terms, and the
accented speech where the old Whisper + Google Translate chain failed. T3–T6 are
chosen on purpose to cover what is still missing: another accent, a fast screen
demo with code, a slow lecture, and a hold-out. Picking the rest from whatever is
at hand would test the files, not the pipeline.

## 2026-11-09 — partial reuse of autopsy-template

From the shared core only the Claude request code is copied:
`claude_client.py`, `structured_call.py`, `token_summary.py`, `batch_client.py`,
their tests and the fake client. `fanout.py`, `shared_context.py` and
`perspectives/` stay out: this pipeline is a sequence of stages over one
transcript, not several independent views of the same input. The API key check
moves from import time to the first Claude call, because cutting audio and speech
recognition need no key.

## 2026-11-09 — repository conventions inherited from Startup Autopsy

Same README order, CLAUDE.md sections, ruff rules (line length 110, target
Python 3.13 here), MIT license, line-ending rules, CI shape (pinned
`ubuntu-24.04`, placeholder API key) and commit style (`Area: what changed`, body
says why). The difference is tooling: uv with a lock file instead of pip and
requirements files. Readers of the series find the same structure in every
project.

## 2026-11-08 — VideoLingo as a benchmark baseline

VideoLingo is the closest open-source tool to this pipeline (speech recognition,
LLM correction and translation, subtitles, dubbing), so it is a baseline on the
same fragments, next to raw Whisper and Whisper + Google Translate. Beating only
the naive chain would prove little; the comparison that matters is against what
a viewer could already use.

## 2026-11-08 — sealed hold-out, external audit, prior-art survey first

T6 is sealed: not listened to, not transcribed for prompt work, run once at the
stage 2 gate with frozen prompts, so the gate numbers are not tuned to the test
set. An external audit reviews the method and results at the gate from stage 2 to
3 and again before v1.0. Before any stage is built, a survey of prior art
(VideoLingo, pyvideotrans and others) settles what already exists, so the project
does not rebuild a solved part and claims only what it adds.

## 2026-11-08 — Definition of Done with per-stage limits

A stage is done when its gate passes: WER ≤ 15% on the accented fragment for
speech recognition; term accuracy ≥ 85%, a blind translation score ≥ 4/5, the
hold-out run and an external audit for correction and translation. Limits are set
in advance: at most 3 runs per hypothesis; stage 1 at most 10 test-set runs and
$5; stage 2 at most 10 correction-prompt versions, 5 translation-prompt versions
and $20; a cost estimate before every full-video run and the owner's confirmation
above $3. After a gate, prompts and the engine freeze and new ideas go to the
Backlog. The limits keep the project from turning into open-ended prompt tuning.

## 2026-11-08 — web UI in v1.1, target language as a parameter

v1.0 is a command-line tool; a local web UI follows in v1.1. The core stays free
of CLI input and output so the UI can call the same functions. The target
language is a parameter from day one (`target_lang`), with no language hard-coded
in the core; only Russian is measured and accepted, because the owner can judge
Russian translations and a blind score needs a reader of the target language.

## 2026-11-08 — what from the test set is published

Published: the test-set README (type of talk, kind of speech, duration, topic,
number of glossary terms) and the glossaries. Not published: the media, the
reference transcripts and run outputs, and no video titles, speaker names,
channels or links. The talks belong to their authors, and naming speakers next
to an "accent" column would single out people who did not agree to it. The
benchmark is reproducible in method, not byte for byte.

## 2026-11-08 — project name: Talk Autopsy

Part of the Autopsy series (Startup Autopsy, Stock Autopsy): each project takes a
subject apart into stages and examines each one honestly. Here the subject is a
technical talk, and the examination is a measurement of every stage between the
audio and the subtitles.

## 2026-11-08 — pipeline shape and pluggable speech recognition

A sequential local pipeline: ffmpeg audio, speech recognition, Claude correction,
Claude translation in overlapping blocks with one glossary per video, then
subtitles, and later dubbing. Every stage reads and writes one JSON format with
fixed segment ids, so stages can be measured and swapped one at a time. Speech
recognition is behind an interface because the engine is the first thing to be
measured: faster-whisper large-v3 on the CPU comes first, others (including a
cloud engine) can be compared on the same audio. Heavy work runs locally; only
text goes to the Claude API.
