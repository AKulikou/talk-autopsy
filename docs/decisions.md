# Decision log

Decisions that shape the project: pipeline, scope, test set, process. Newest
first. Experiments and their numbers go to [experiments.md](experiments.md); this
log records what was decided and why.

Entry format: date, decision, why, and what it rules out.

## 2026-11-11 — Speaker details out of tracked files; history rewritten before going public

Context: external audit #1 (finding 13) found the speakers of the test-set and
operational videos identifiable from tracked files: names, a job title,
accent labels next to product names, a course code.

Decision: tracked files carry fragment IDs only (T1, T2, T6, real-01,
stress-01) and generic descriptions ("strong non-native accent"); names and
identifying descriptions were removed on 2026-11-11, and
`scripts/check_names.py` checks every tracked file against a local,
git-ignored list drawn from the owner's notes (`work/names.local.txt`). **Before
the repository is made public, its history is squashed or rewritten**, so
that old commits do not carry these details. Not done now: the repository is
private, and rewriting history before the T6 run would move the tags the run
depends on.

What it rules out: making the repository public with its current history.

## 2026-11-11 — pipeline-v3 refrozen after external audit #1; T6 runs on it

Context: the audit found prompt examples taken from the test set, a lenient
term-accuracy metric, no interpretation rule for T6, a cache that a
validator fix did not invalidate, and a key-term limit that raised an error.

Decision: the fixes that must precede T6 were made in one session and the
pipeline refrozen as tag `pipeline-v3` (d180a73): `glossary-auto-v2` and
`translation-v2.1` (same rules, neutral examples), cache key with a code hash,
key-term truncation, term accuracy v2. The T6 protocol (rule, intervals,
overfitting signal, scope, translation check) was written before the run
(experiments, T6 protocol). The human check of the T6 reference that the
audit asked for is not possible (no fluent listener; owner's decision); it is
recorded as a limitation.

What it rules out: running T6 on `pipeline-v2`; any change to a frozen path of
`pipeline-v3` before the T6 run.

## 2026-11-11 — translation-v3 not adopted; v2 stays the default

Context: translation-v3 (term classes keep / both / translate / ui) was built
as an option on 2026-11-10, with a decision rule written before the owner's
blind rating of 16 v2/v3 pairs: v3 is adopted if it wins more pairs than v2
and none of its scores of 2 or less is caused by lost meaning.

Decision: **translation-v3 is not adopted**. The owner's blind scores
(un-blinded by `scripts/c1b_rating.py`): v3 won 5 pairs, v2 won 6, 5 ties;
mean v2 4.44, v3 4.25; the lowest v3 score is 3. The first condition fails.
translation-v2 stays the default and the frozen version (`pipeline-v2`); no
new freeze tag. translation-v3 remains available as `--translation v3`.

Why: by the rule fixed in advance. The rating suggests v3's idea works where
the Russian word is short and natural (frontier model, prompt as a noun,
inference, UI names with a short gloss) and fails where the gloss is long, an
acronym is expanded, or familiar jargon is replaced (experiments, C1b, Owner's
blind rating). A translation-v4 along those lines is in the Backlog, after
the T6 run.

What it rules out: changing the default translation before the T6 run.

## 2026-11-10 — T6 reference in two variants, gate with bounds

Context: the blind audio judge failed its T2 calibration as the sole
adjudicator (79.0% as specified, 82.5% after the labelled post-hoc
rescoring; threshold 90%), the owner has withdrawn from adjudication, and the
assistant cannot decide T6's spans.

Decision (owner): the judge decides T6's spans anyway, and the reference is
built two ways: J, the judge's text in every judged span; M, the engines'
majority where one exists and the judge only on split spans. Spans where the
judge alone decided or where J and M differ are uncertain. The term-accuracy
gate uses bounds: glossary-term occurrences inside uncertain spans counted as
misses (lower bound) or hits (upper bound). Pass if the lower bound >= 85%,
fail if the upper bound < 85%, otherwise inconclusive; J, M and both bounds
are reported. Built 2026-11-11 (experiments, T6 protocol).

Why: the conclusion must not depend on whether the judge is right where it
alone decides. An inconclusive result is reported as such.

What it rules out: a single judge-decided T6 reference presented as checked;
re-judging T6 with another prompt after seeing the T6 run.

## 2026-11-10 — Translation term policy (keep / both / translate / ui)

Context: the owner watched a whole interview (real-01) with
translation-v2 subtitles and found ordinary words left in English or turned
into odd loanwords ("tokens", "compute", "inference-компании", "промптнуть",
"эмерджентные свойства"). translation-v2's rule "technical terms stay in
English" does not separate names and code from words with a normal Russian
translation; the same flaw had been noted for UI names.

Decision (owner's policy): four classes of terms. `keep`: Latin script as is
(names, code, commands, identifiers, file names, acronyms without a Russian
form). `translate`: Russian only. `both`: Russian with the English in
brackets at the first occurrence in the video, Russian only afterwards (jargon
without a good Russian word). `ui`: English as on screen with a Russian gloss
in brackets at the first occurrence. The policy lives in the glossary (a
`policy` column; user glossaries win), terms without one are classified by
one Haiku call after the auto-glossary, and the first occurrence is decided in
code, not by the model. Implemented as `translation-v3`, an option
(`--translation v3`); the default stays translation-v2 until the owner's blind
rating of 16 v2/v3 pairs (experiments, C1b, decision rule written before the
rating).

What it does not change: the speech recognition path, the auto-glossary
extraction, correction-v2 and sentence units, which decide the English
transcript and the T6 number, are untouched (`glossary.md` and the key terms
are not rewritten; tested). `pipeline-v2` stays the frozen version; adopting
v3 needs a short follow-up that flips the default and tags a new freeze.

## 2026-11-10 — Audio judge: repetition convention made explicit, post-hoc rescoring (still failed)

Context: the blind audio judge failed its T2 calibration on 2026-11-10
(agreement 79.0% against the 90% rule; WER between the two T2 references
1.90% against 2%). Of the 24 disagreements, 14 were repetitions and false
starts the judge writes and the T2 reference omits, 5 were formatting
(`Shift+Enter`, `3,000`), 5 one-word substitutions. The reference convention
on repetitions had never been written down.

Decision: the convention is made explicit (`data/test-set/README.md`:
immediate repetitions and restarts written once, no thousands separators, key
combinations as `Shift+Enter`, each row marked as added after the
calibration) and applied in code by one deterministic function,
`judge.normalize_for_adjudication`, symmetrically to both sides of every
comparison and to the judged spans when a reference is built from a judged
file. The existing T2 judge output was rescored with no new requests and the
threshold unchanged. **This is a post-hoc methodology change, made after
seeing the result; the external audit will see it.** Both results are
published side by side (experiments, Post-hoc rescoring): 79.0% before,
82.5% after (owner's 19 by ear: 14), WER 1.17%. The rule still fails, so the
judge is not accepted for T6 and T6 is not judged.

Not adopted: the planned two-reference scoring of T6 (judge reference and an
engine-majority reference, the gate conclusion accepted only if both give the
same side of 85%) belonged to the T6 reference step, which did not run; it
is not in the T6 protocol. The judge's prompt (`audio-judge-v1`) is unchanged,
and the README rows added after the calibration are kept out of it. The
metrics normaliser and `benchmark.csv` are unchanged.

What it rules out: a further rule fitted to the remaining T2 disagreements
(7 of the 20 are repetitions split by the span edge; even counted, agreement
would be 85.1%). T6's reference needs another route; owner decision.

## 2026-11-10 — Paid Gemini tier for the audio judge

Context: the blind audio judge (evaluation tooling, `judge.py`) needs one
Gemini request per disputed span: 114 for the T2 calibration, 86 for the T6
reference. The free tier allows 20 requests per day per project for
`gemini-3.8-flash`, and that quota stopped the T2 calibration before its first
span (first attempt, 2026-11-10); at that rate the two fragments needed at least 10 days.

Decision: the owner moved the Gemini project to the paid tier (Tier 1) with a
monthly spend cap of EUR 5 in AI Studio. The judge uses the most capable model
the key offers, `gemini-4-carbon`, with the lowest thinking level (`low`): a
model different from the one the Gemini STT engine uses (the CLI refuses the
engine's models). The brief asked for the newest Pro model, but
`gemini-3.1-pro-preview` answers 404 for this key, and `gemini-2.5-pro` will
route to `gemini-3.8-flash`, the STT engine's model, from 2026-12-01. Each
session caps the judge at $3 by its own counter (cost per response from the
usage metadata and a dated price table in `config.py`); the AI Studio cap is
only a backstop, because its spend display lags by hours.

What it does not change: the production pipeline still uses AssemblyAI
(`pipeline-v2`), and Gemini remains its fallback engine on whatever tier the
key has. Records judged by another model (the free-tier pilot) are kept apart
in `judge.free-pilot.jsonl` and never mixed into a calibration.

## 2026-11-10 — translation-v2 adopted

Owner's blind pairwise comparison C1 (`work/T2/C1-blind.md`, segments 1–15 of
20; experiments, "C1 (blind)"): translation-v1 mean 4.40, translation-v2 mean
**4.67** over 15 pairs; v2 better in 8, v1 in 3, ties 4. Both are above the
stage 2 target of 4.0, so the C1 criterion is met, and v2 is the better of the
two. `translation-v2` stays the default and is the version frozen for T6.

What it rules out: returning to the compression rule of v1. What is left open:
v2's two weak items were sentence fragments cut at segment boundaries; they are
addressed in the subtitle and unit layer (sentence units), not in the prompt.

## 2026-11-09 — Refusal fallback on in `run`, off in experiments

Owner instruction of 2026-11-09, revising the entry "no
server-side refusal fallback" below: `talk-autopsy run`, the tool a viewer
uses, sends `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`)
on the Sonnet correction and translation calls, so a block the classifiers
decline is answered by the fallback model instead of staying untranslated;
the serving models are recorded in `meta.<stage>.served_by`. The experiment
commands (`fix`, `translate`, and therefore every `eval` row) keep it off,
so a benchmark row still describes one named model. The Haiku auto-glossary
call has no server-side fallback (the API offers none for Haiku).

## 2026-11-09 — No meaning compression in translation; subtitles up to 3 lines

Owner decision after rating C1 (`work/T2/C1-rating.md`, mean 3.85 against the
target of 4.0). All three segments rated 2 lost or distorted meaning, and the
cause in each was the compression rule of `translation-v1` ("compress where
the target text would be much longer"). From `translation-v2` on, translation
keeps every content clause; only disfluencies (fillers, repetitions, false
starts) may be dropped. Reading speed is handled in the subtitle layer: cues
may hold up to 3 lines, every `.srt` gets a sibling `.ass` file with a smaller
font so three lines fit, and reading speed is reported per file, never
enforced by cutting meaning.

What it rules out: shortening a translation to meet a characters-per-second
limit. What it costs: some cues will be read faster than 17 characters per
second; the share is reported so the trade-off stays visible. Hypothesis D1
(compression for reading speed) is answered for now by this decision.

## 2026-11-09 — Gate 1 -> 2 passed: AssemblyAI with key terms as the main engine

Owner's choice after the stage 1 summary (`docs/experiments.md`). AssemblyAI
Universal-3.5 Pro with the glossary as key terms reached 99.1% term accuracy
on T2 (100% on T1) and WER 4.43% on T2, in about 26 s per 10 minutes of audio
(about 23x real time) at about $0.26 per hour of audio list price. It replaces
the earlier "Gemini as the main engine" direction and the gate entry below
that named Gemini: Gemini's free tier ran into 503 "high demand" answers and
its daily quota within one 28-minute talk, and `gemini+align` took about 65
minutes per hour of audio against the 30-minute limit. Fallbacks, in order:
Gemini + forced alignment (free tier), then faster-whisper large-v3 (local).

Caveat: the 99% used a glossary reviewed against the reference, so every term
was known to occur. The realistic number comes from a glossary drafted
automatically from the first transcript (B5) and from the sealed hold-out T6.

What it rules out: Gemini as the main path, and a pipeline step that waits on
the free tier's daily quota. What it costs: audio of every video goes to
AssemblyAI (a paid service; the account's free credit covers it for now).

## 2026-11-09 — no server-side refusal fallback on the Claude stages

The API offers a server-side fallback (`fallbacks: "default"`) that re-runs a
request on another model when Sonnet 5.5 or Opus 5.5 declines it. The
correction and translation stages do not use it. Every stage 2 number is a
measurement of one named model (B1 Sonnet, B4 Opus); a silent switch to another
model inside a call would make a row in `results/benchmark.csv` describe a model
that did not produce it. A refusal is already a hard error in
`core/structured_call.py`, so a declined block fails visibly instead. Decided
when the correction stage was written (the Backlog item asked for that), by
the assistant during the unattended session of 2026-11-09; the owner may
reverse it for the v1.0 tool, where availability matters more than attribution.

What it rules out: a run that completes on a different model without saying
so. What it costs: a block a classifier declines stays uncorrected (and is
listed in `meta.correction.failed_blocks`).

## 2026-11-09 — Gate 1 -> 2 passed: Gemini as the main engine

Owner decision of 2026-11-09, given in advance that day: if the stage 1 summary shows Gemini under the WER limit on T2, stage 1
closes with Gemini as the main engine and stage 2 starts. The numbers: Gemini
`gemini-3.8-flash` WER 2.34% on T2 (limit 15%) and 1.41% on T1; term accuracy
93.5% / 99.2% without a glossary. Used as `gemini+align` (chunked Gemini plus
forced alignment); faster-whisper large-v3 is the fallback. The engine
freezes with the gate: speech recognition changes from here on go to the
Backlog. Recorded by the assistant on the owner's instruction; the reference
biases listed in the stage 1 summary stay attached to these numbers.

## 2026-11-09 — Gemini as the main engine (owner direction, pending the gate)

The owner chose Gemini as the main speech recognition engine, on the free tier
only, with faster-whisper as the fallback. Stage 1 numbers
([experiments.md](experiments.md), Stage 1 summary): Gemini `gemini-3.8-flash`
has the lowest WER on both fragments (T2 2.34%, T1 1.41%) and reaches the
stage 2 term-accuracy target without a glossary (T2 93.5%, T1 99.2%), against
faster-whisper's 6.19% / 61.7% on T2. Gemini's timestamps are model estimates,
so it is used with forced alignment (`gemini+align`, A5 (setup)), chunked for
long audio with a per-chunk cache and a model fallback list. faster-whisper
stays as the per-chunk fallback and as the local engine for audio that must
not leave the machine.

What it rules out: paid Gemini tiers (a quota or billing error stops a run
instead of switching to a paid model), and treating AssemblyAI as the main
engine despite its keyterms result (it costs money per hour and was not the
owner's choice). What remains open: the formal decision at the stage 1 gate;
how often the free tier's 503 "high demand" answers force the fallback on a
real run (the full-talk smoke test in A5 (setup) is the first data point).

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
