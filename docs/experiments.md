# Experiment log

Every measured run on the test set gets an entry here, newest first, and a row in
[results/benchmark.csv](../results/benchmark.csv). Decisions about scope and
process are in [decisions.md](decisions.md).

Entry format:

- **ID:** the hypothesis name from the project plan: A1–A5, B1–B5, C1–C5,
  D1–D4. A rerun of the same hypothesis gets a letter suffix (`A1b`); a
  preparatory run that tests nothing gets `(setup)` (`A1 (setup)`).
- **Hypothesis:** what is expected and why, written before the run.
- **Run:** fragments, engine or prompt version, models, settings, date, commit.
- **Numbers:** the metrics that the hypothesis is about, next to the baseline.
- **Conclusion:** what the numbers show, including when they show nothing.
- **Decision:** keep, revert, or try something else; anything new goes to the
  Backlog below, not into the current stage.

Limits (from CLAUDE.md): at most 3 runs per hypothesis; stage 1 at most 10
test-set runs and $5; stage 2 at most 10 correction-prompt and 5 translation-prompt
versions and $20; a cost estimate before every full-video run.

## Evaluation at a glance

The six claims the README rests on, how much evidence each has, who decided
the reference or the score, and whether the rule was written before the
result.

| Claim | n | Who decided | Pre-registered? | Result |
|---|---|---|---|---|
| Term accuracy ≥ 85% on the hold-out T6 (the stage 2 gate) | 72 term occurrences, 1 run | references: Gemini audio judge (failed its calibration) and engine majority; no human check | yes: T6 protocol, written before the run (2026-11-11) | 72/72 (J), lower bound 66/72 = 91.7%, Wilson 83.0–96.1%: **pass (not demonstrated)** |
| Term accuracy on T2 | 107 occurrences, 3 runs | reference: owner by ear (19 spans), rules, engine majority | no: the development fragment (prompts iterated on it) | 94.4–96.3% micro, 84.6–89.7% macro |
| Key terms do the work | T2 107 and T6 72 occurrences, existing transcripts | as the two rows above | no: ablation after the results | T2 80.4% -> 96.3%; T6 71/72 -> 71/72 (no change; correction added the last) |
| Pipeline vs a single Gemini call on terms (T2) | 107 occurrences, 3 runs vs 1 call | as T2 | no | 94.4–96.3% vs 93.5%: the lead is within the pipeline's own spread |
| Absolute translation quality on T6 (mean ≥ 4.0) | 12 units, 1 rater | owner, blind, single-rated | yes: before the run, amended twice before the run | 4.42: **met**; not better than a plain Gemini translation (+0.08, −0.33 … +0.50) |
| Specification C1: at least 1 point above Google Translate | 12 units, 1 rater, two days | owner, blind; anchor drift 0.00 | yes: Translation rating, before any Google Translate call (2026-11-11) | 0.00 (−0.42 … +0.42): **not met** |

## Metrics

The one place where the metrics are defined; the README and the other entries
refer here. Code: `talk_autopsy/metrics.py`; columns of
[results/benchmark.csv](../results/benchmark.csv): `evaluation.BENCHMARK_COLUMNS`.
Every text is normalized by `metrics.tokenize` (fillers removed, edge
punctuation stripped, nothing expanded).

- **WER:** (substitutions + deletions + insertions) / reference words, from the
  minimum-edit word alignment of the lower-cased tokens (jiwer). Unchanged.
- **Term accuracy (v2, the headline since 2026-11-11):** for each occurrence of
  a glossary term in the reference, a hit only if the hypothesis has the same
  term **in the same place**: the reference tokens of the occurrence are
  aligned (the WER alignment) to consecutive hypothesis tokens that form an
  occurrence of the term. Identifiers match case-sensitively, the last word
  may be a regular plural or a possessive (`Anthropic's`). Reported as
  - `term_accuracy_v2`: micro-average, hits / occurrences;
  - `term_accuracy_v2_macro`: the mean of each term's hit share, every term
    weighing the same;
  - `term_accuracy_v2_without_top`: the micro-average without the most frequent
    term (on T1 and T2 that is `Claude Code`: 42 of 125 and 28 of 107
    occurrences);
  - `term_precision` and `term_false_positives`: term occurrences in the
    hypothesis (any glossary term, also those absent from the reference) that
    match no reference occurrence in place.
- **Term accuracy v1 (superseded, column `term_accuracy_v1`):** position-free:
  per term min(occurrences in the reference, occurrences in the hypothesis), no
  possessives. A term missed where it was said but written elsewhere counted
  as a hit (independent LLM review #1, finding 3). Kept for the record.
- **False-edit rate (column `false_edit_rate_v2`):** the share of the
  correction's word edits that move the text further from the reference. The
  edits are the word substitutions, deletions and **insertions** of the
  minimum-edit alignment of the correction's input to its output, one per
  word; an edit is false if applying it alone to the input increases the
  input's word errors against the reference. Empty when the correction changed
  no word (a rate of 0 then would be meaningless). Invented inserted words are
  false edits.
- **False-edit rate v1 (superseded, column `false_edit_rate_v1`):** the share of
  the reference words the raw transcript had right that the correction broke.
  Not a share of edits, and blind to inserted words (finding 9).
- **Reading speed:** characters per second per subtitle cue (shares above 17
  and 25 cps), reported by the `srt` step; not in the benchmark table.
- **Blind translation score:** the owner's 1–5 rating (C1 entries).
- **Not implemented:** timing drift (`metrics.timing_drift` raises; no
  hand-labelled timestamps exist) and term accuracy of the translation. The
  never-filled `timing_median_ms` column was removed from the table on
  2026-11-11.

**Re-scoring, 2026-11-11** (`scripts/rescore_benchmark_v2.py`, no API call).
Every current T1/T2 row was re-scored from the cached output it was recorded
from; before writing, the script recomputed WER and term accuracy v1 and
checked that both equal the recorded values (all 19 did), so the file is the
one that was scored. Superseded rows (draft glossary, T1 before the `@claude`
fix) keep their v1 numbers, get `status` = superseded and no v2 numbers.

| Row | v1 | v2 micro | v2 macro | v2 without `Claude Code` | precision | false-edit v1 -> v2 |
|---|---|---|---|---|---|---|
| T2 AssemblyAI + auto terms + correction-v2 (B1 v2) | 96.26% | 96.26% | 89.74% | 94.94% | 99.04% | 0.00% -> no edits |
| T1 AssemblyAI + auto terms + correction-v2 (B1 v2) | 98.33% | 98.40% | 97.30% | 97.59% | 100.00% | 0.00% -> 0.00% (1 word edit; the case-only edit is not a word edit) |
| T2 faster-whisper + correction-v1 (B1) | 94.39% | 93.46% | 84.25% | 91.14% | 98.04% | 0.00% -> 0.00% (0 of 65 word edits) |
| T1 faster-whisper + correction-v1 (B1) | 98.33% | 98.40% | 97.30% | 97.59% | 95.35% | 0.00% -> 2.75% (3 of 109 word edits) |
| T2 AssemblyAI, no key terms (A3 (setup)) | 80.37% | 80.37% | 81.78% | 89.87% | 98.85% | |
| T1 faster-whisper (A1 (setup)) | 54.17% | 52.80% | 78.83% | 79.52% | 100.00% | |

The micro-average barely moves (position-awareness changes at most 1 point,
possessives add occurrences on T1); the **macro-average is 6.5 points lower on
T2** (four rare terms are each missed once: daltonized, at-mention, WebFetch,
stand-up), and the false-edit rate of correction-v1 on faster-whisper T1 input
is 2.75%, not 0: the old definition did not see inserted words.

## Spec criteria

Every acceptance criterion of the project specification (hypotheses A1–A5,
B1–B5, C1–C5, D1, the metric targets, the judge calibration rule), next to the
result. Criteria are quoted from the project plan (in Russian, not published),
translated without rewording. Status is `met`, `not met` or
`not run`, nothing in between; where a decision went ahead despite `not met`,
the last column says so and why. Written 2026-11-11 after independent LLM review #1
(finding 7: criteria had been reworded after results); C1, term accuracy,
translation quality and the gate rows updated after the T6 translation check
(2026-11-11); C1 updated after the Google Translate rating (2026-11-12). Numbers: term accuracy
v2 where the row was re-scored (Metrics above); T2 is the fragment that
carries the weight (T1's reference was decided by a Claude model).

| ID | Criterion (specification) | Result | Status | Decision despite "not met" |
|---|---|---|---|---|
| A1 | WER on the accented video below 15% | faster-whisper large-v3 on T2: 6.19% | met | |
| A2 | glossary-term recognition up by at least 20 points with the glossary in `initial_prompt` | T2 61.7% -> 51.4% (−10.3); T1 55.1% -> 96.6% (v1) | not met (on T2, the accented fragment; met on T1) | Whisper kept only as a fallback engine |
| A3 | cloud STT: WER gain over 5 points at up to $1 per hour | T2: AssemblyAI 6.97%, Gemini 2.34% against Whisper 6.19% (best gain 3.85 points); T1: gain 9.79 points both; references built with these engines (biased in their favour) | not met (on T2; met on T1) | AssemblyAI with key terms chosen as the main engine (owner): key terms gave T2 99.1% term accuracy with a reference glossary, 96.3% with the auto-glossary |
| A4 | Gemini with audio: equal or better term accuracy than the best block B result at comparable price, with the same glossary | Gemini ran without a glossary, to build the references (T2 93.5% against 96.3% for the B path) | not run (as specified) | |
| A5 | mean onset shift under 0.3 s against hand labels on 10 minutes | no hand labels exist | not run | |
| B1 | term accuracy after correction above 90%, WER not worse | T2 AssemblyAI + auto terms + correction-v2: 96.26% (v2), WER 4.73% -> 4.73% | met (the number is the recogniser's; correction made 0 edits) | |
| B2 | fewer than 5% of edits distort meaning (all edits on 10 minutes checked by hand) | correction-v1: 0 of 3 on T2; correction-v2: 0 edits on T2, 0 of 2 on T1 | met (on 3 and 2 edits: too few to say much) | |
| B3 | recognition alternatives or word confidence: at least +3 points term accuracy | | not run | |
| B4 | Sonnet enough: term-accuracy difference to Opus under 2 points | T2 Gemini+align input: Opus 100% vs Sonnet 96.3% (gap 3.7 points, v1) | not met (gap 3.7 > 2) | Sonnet kept for cost (0.060 vs 0.129 USD per fragment) |
| B5 | auto-glossary covers at least 70% of the hand glossary | T2 74.4% / 79.5%; T1 56.8% / 62.2% (two runs each) | met on T2, not met on T1 | auto-glossary adopted as the pipeline's glossary step (an unseen video has no hand glossary) |
| C1 | Claude's blind mean at least 1 point above Google Translate and not below VideoLingo; 20 random fragments, three systems mixed | **Against Google Translate** (2026-11-12, pre-registered, 12 T6 units instead of 20 fragments, blind, single-rated; Google Translate items rated on another day, anchor drift 0.00): pipeline 4.42, Whisper + Google Translate 4.42, difference 0.00 [−0.42, +0.42] ([result](#translation-rating-pre-registered-2026-11-11-before-any-google-translate-or-rater-call)). VideoLingo: not run, out of v1.0 scope (owner, 2026-11-11; [why](#baselines-on-one-set-t2-and-t6)). Earlier: translation-v1 rated 3.85 alone (not blind); v1 against v2 blind-pairwise (not fully blind), v1 4.40, v2 4.67; **absolute translation quality on T6** (a criterion added on 2026-11-11, not this one; first called "C1 on T6") against a plain Gemini translation: pipeline 4.42, Gemini 4.33, difference +0.08 [−0.33, +0.50] | **not met** against Google Translate (difference 0.00 < 1.0); VideoLingo part not run (out of v1.0 scope) | Accepted as a finding (2026-11-12): not confirmed; no translation changes before v1.0; translation-v4 idea stays in Backlog (decisions). Gate 2 -> 3 (2026-11-11, before this rating) was decided on term accuracy, the stage's criterion; the absolute translation quality on T6 (mean >= 4.0) was met there |
| C2 | one translation per glossary term across the video (20 key terms) | | not run | |
| C3 | no lost or repeated phrases at 10 block boundaries, with and without overlap | | not run | |
| C4 | the style instruction preferred in 2 of 3 cases, blind | | not run | |
| C5 | Batch API: identical quality, 40–50% cheaper | | not run | |
| D1 | (reworded 2026-11-09, after C1) no more than 10% of cues faster than 20 cps with three lines | T2 fragment: 15.8% of Russian cues above 25 cps (so more than 10% above 20); real-01 14.8% above 25 | not met | nothing is cut for reading speed (owner's decision: meaning first; `.ass` with a smaller font) |
| WER | ≤ 10% clean speech, ≤ 15% accent | AssemblyAI + auto terms: T1 0.58%, T2 4.73% | met | |
| Term accuracy | ≥ 85% after correction | T2 (development fragment) 96.26% (v2; macro 89.74%, without the top term 94.94%); `pipeline-v3` T2 repeats 94.39–96.26% micro (macro 84.62–89.74%: one repeat below 85% on macro; the target is defined on micro); T6 72/72 against J and 71/71 against M, lower bound 66/72 = 91.67% (Wilson 83.0–96.1%) | met (on T6 "pass (not demonstrated)") | |
| False edits | ≤ 3% | false-edit rate (one definition, Metrics): 0 or no edits on every current row except T1 faster-whisper + correction-v1, 2.75% | met | |
| Translation quality | blind owner rating of 20 random lines, mean ≥ 4.0 | translation-v1 3.85 (not blind); v2 4.67 only in a pairwise comparison against v1; `pipeline-v3` on T6: **4.42** (12 random units of at least 6 words, blind, single-rated, mixed with a Gemini baseline) | met (on T6, 12 units instead of 20: owner's amendment before the run). This is the absolute translation quality criterion, not C1: C1 (against Google Translate) is not met (row C1) | |
| Reading speed | ≤ 17 cps, at most 5% of cues above | T2 fragment 58.8% of Russian cues above 17 cps; real-01 49.2% | not met | as D1 |
| Timing shift | median ≤ 300 ms against hand labels | no hand labels; timing drift not implemented | not run | |
| Cost | ≤ $3 per hour of video | 0.47 USD AssemblyAI + 0.67–0.82 USD Claude per hour (three whole videos) | met | |
| Time | ≤ 30 min per hour of video | 9.1–9.9 min per hour (three whole videos) | met | |
| Judge calibration | (added 2026-11-10) the audio judge is accepted for T6 if agreement on T2 ≥ 90% and WER between the T2 references ≤ 2% | 79.0% (WER 1.90%); 82.5% after a labelled post-hoc rescoring (WER 1.17%) | not met (failed twice) | deviation: the rule said T6 is not judged; T6 was judged anyway, with two references (judge / engine majority) and bounds (owner's decision 2026-11-10) |
| Gate 2 -> 3 | term accuracy ≥ 85% on the hold-out T6, run once | T6 run once 2026-11-11: 72/72 (J), 71/71 (M), lower bound 91.67%, "pass (not demonstrated)"; absolute translation quality 4.42; review #1 done ([Gate 2 -> 3](#gate-2---3-2026-11-11)) | met | passed by the owner's decision 2026-11-11, on term accuracy (the stage's criterion), not on translation against a baseline |

## v1.0 checklist

The specification's list for v1.0 (stage 3), with the state of each item.

| Item | State |
|---|---|
| the owner watched two full videos and uses the tool | done (owner) |
| `benchmark.csv` has numbers for every closed hypothesis; the README compares the system, raw Whisper, Whisper + Google Translate and a plain Gemini translation on one set (amended 2026-11-11, owner: VideoLingo out of v1.0 scope) | [Baselines on one set](#baselines-on-one-set-t2-and-t6): all four measured on T2 and T6; Google Translate run 2026-11-11 and rated by the owner 2026-11-12 (4.42, equal to the pipeline). VideoLingo: not run, out of v1.0 scope (its installer was blocked by the permission filter and not bypassed; its default recogniser is now Qwen3-ASR) |
| CI green; README in the series structure; honest Known limitations | in progress |
| re-run from cache within a minute | **met**: 6.7–8.9 s wall time on T2 (three re-runs, below) |
| independent LLM review #2 | done 2026-11-12 ([findings and responses](review-2026-11-12.md)); fixed except the history rewrite (next) |
| tag `v1.0` | not done |

**Re-run from cache (2026-11-11).** The work folder of the `pipeline-v3` T2 repeat 2 (a fresh run
from a clean worktree of the tag, Freeze pipeline-v3, Repeats) was copied to `work/T2-v3-cache/` and
`talk-autopsy run data/test-set/T2.wav --lang ru --name T2-v3-cache` was run three times from the main
checkout (HEAD 67bb53e, frozen paths equal to the tag), with well-formed but invalid API keys in the
environment so that a stale step would fail instead of spending. Every model step was skipped as up to
date (stt, glossary, stt2, align, fix, translate: the stamps are content hashes plus the code hash, so a
copied folder counts as cached); only the subtitles were rendered (about 1 s), byte-identical to the
original run's `.srt` files. Wall time from start to exit, including `uv run` and Python start-up:
**7.4 s, 8.9 s, 6.7 s**; 0 USD. No code change was needed. Not covered: `--output mp4-burned`
re-encodes the video on every run (about 0.27x the video's length; rendering, not a model call), so a
launcher re-run with the TV file takes minutes, not seconds (Backlog).

## Baselines on one set (T2 and T6)

The specification's comparison for v1.0: Talk Autopsy (`pipeline-v3`) against raw Whisper,
Whisper + Google Translate and a plain Gemini translation on one set. **VideoLingo is out of v1.0
scope** (owner's decision 2026-11-11; the specification's Definition of Done amended): running its
downloaded installer was blocked by the coding agent's permission filter and the project chose not to
bypass it; it had also switched its default recogniser from WhisperX to Qwen3-ASR since the
specification was written. The attempt is recorded below. The set is **T2 and T6**: both have
references, and T6 is no longer sealed after its single run. The same metrics everywhere (Metrics
above): term accuracy v2 (micro, macro, without the top term, precision) and WER of the English;
on T6 against reference J, with reference M as the range. The translations are compared by the
owner's blind rating on 12 T6 units ([Translation rating](#translation-rating-pre-registered-2026-11-11-before-any-google-translate-or-rater-call),
rated 2026-11-12): pipeline 4.42, Whisper + Google Translate 4.42, plain Gemini 4.33.

| English side | T2 term accuracy v2 | T2 WER | T6 term accuracy v2, J / M | T6 WER, J / M |
|---|---|---|---|---|
| Talk Autopsy `pipeline-v3` | 94.39–96.26% (3 repeats, mean 95.33%) | 4.24–4.48% | 100.00% / 100.00% (one run) | 4.70% / 2.89% |
| raw Whisper (faster-whisper large-v3, no glossary) | 61.68% | 6.19% | 87.50% / 88.73% | 8.01% / 5.97% |
| Whisper + Google Translate (run 2026-11-11) | 61.68% (raw Whisper's transcript) | 6.19% | 87.50% / 88.73% | 8.01% / 5.97% |
| VideoLingo (commit 61d944a, defaults, Claude Sonnet 5.5) | not run, out of v1.0 scope | | not run, out of v1.0 scope | |

**Raw Whisper (2026-11-11, no new run).** T2: the existing row (A1 (setup), re-scored with v2).
T6: the faster-whisper transcript made for the reference consensus
(`work/T6/raw.whisper-large-v3.json`, large-v3, int8, CPU, no glossary prompt; 30.8 min wall
time then), scored with `eval T6 --record --engine faster-whisper-large-v3 --stage stt`. T6
under v2: macro 87.84% (J) / 89.60% (M), without the top term 88.06% / 89.39%, precision 100%;
bounds 86.11–94.44%. **Bias:** on both fragments this engine was the backbone of the consensus
that built the reference (one of three engines), so its WER and term accuracy are biased in its
favour. Its term accuracy is much higher on T6 than on T2 (87.5% against 61.7%): T6's glossary was
drafted by Gemini from reference J, and T2 has more product names, which raw Whisper mishears most.

**Whisper + Google Translate (2026-11-11): run.** `talk_autopsy/baselines/google_translate.py` (evaluation tooling, not frozen): the raw faster-whisper
transcript above, cut into sentence units by the pipeline's own `units.make_units`, each unit sent to the
Google Cloud Translation API, Basic edition (v2, `format: text`, the key in the `X-goog-api-key`
header), and written as `ru.json` + `.srt/.ass` with the pipeline's subtitle layout. So its English side
is exactly raw Whisper's and only the translation differs from what the owner will rate. Run:
`uv run python scripts/google_translate_baseline.py` (T2 and T6, outputs in
`work/baselines/google-translate/`, a 100,000-character cap); about 21,700 characters for both fragments
(T2 41 units, 10,660; T6 156 units, 11,022), inside the free tier of 500,000 characters per month.
The key is registered in `talk_autopsy/secret_registry.py`, not in `config.SECRET_ENV_VARS`: `config.py`
is a frozen path of `pipeline-v3`. Tests use a fake caller and a fake `urlopen` (no network).

*Run (2026-11-11).* The key (restricted to the Cloud Translation API, free tier) was added to
`.env`; the script now counts every character before the first request and sends nothing above
`--max-chars`. Planned and sent: **T2 10,660** characters (41 units, 3 requests), **T6 11,022** (156
units, 3 requests) and the **12 T6 rating items, 668** (1 request; Translation rating below): **22,350
characters, 0 USD** (free tier; 0.45 USD at the list price beyond it). Outputs (git-ignored):
`work/baselines/google-translate/T2/` and `T6/` (`ru.json`, `.ru.srt`, `.ru.ass`) and
`work/baselines/google-translate/T6-units.json` (unit id, span, raw Whisper English, translation). Every
unit came back in Cyrillic, without HTML entities. The English side is raw Whisper's, so its rows in
`benchmark.csv` (`faster-whisper-large-v3 + google-translate-v2 (baseline)`, stage `translate`) copy
raw Whisper's metrics; the translation does not change them. Its translation is compared only through
the owner's rating.

**VideoLingo (2026-11-11): attempted, not run.** VideoLingo (Apache-2.0) was cloned into
a folder outside the repository, commit
`61d944a46df6970176191606a89e646122da8f0a` (2026-11-10, version string 3.1.1 in its `config.yaml`).
Its defaults have changed since the specification was written: local recognition is now
**Qwen3-ASR 1.7B with Qwen3-ForcedAligner** (WhisperX is no longer installed by its installer), on
Python 3.12 with PyTorch 2.8 CPU, installed by `uv run start.py` (`setup_env.py --yes`); scripted
runs go through its local HTTP API (`start.py --api`, `POST /input`, `POST /run`). The plan was its
defaults with the LLM set to `claude-sonnet-5-5` through Anthropic's OpenAI-compatible endpoint
(`https://api.anthropic.com/v1/`; VideoLingo sends no `response_format` by default, which matters
because the endpoint accepts only a strict `json_schema` there), behind a local logging proxy so that
the key stays in memory and the token usage is recorded exactly. **The first installation attempt
was refused by the coding agent's permission policy** (Claude Code auto mode: running code downloaded from
outside the project, "Code from External"), before any package was installed. That is a policy
decision for the owner, not a technical failure, so it was not retried or worked around; the second
attempt is left to the owner (below). No API key was written to any file; LLM cost 0 USD.

**Out of v1.0 scope (owner's decision 2026-11-11).** The installer was not retried and the
permission filter was not bypassed; its default recogniser is now Qwen3-ASR. The notes below are
kept for a later version. To run it: either the owner allows the coding agent to run the VideoLingo installer, or runs it
once by hand (`uv run start.py --api` in the VideoLingo folder) and a later
run drives the API on `data/test-set/T2.wav` and `T6.wav`; estimate for two 10-minute fragments
with Sonnet 5.5: under 1 USD (its summary, term extraction, meaning split, two-step translation and
alignment calls), Qwen3-ASR on the CPU of the order of the fragment length or more (not measured).

### Translation rating (pre-registered 2026-11-11, before any Google Translate or rater call)

The README's translation column: Talk Autopsy against Whisper + Google Translate and a plain Gemini
translation on one set, rated by the owner. To keep the owner's work small, the pipeline and Gemini
scores are the ones he already gave (T6 translation check above); the new rating adds only the
Google Translate items and four repeated items to measure drift between the two days. Written and
committed before any call to Cloud Translation or to the Gemini rater.

- **Set:** the 12 T6 units of the T6 translation check (sample seed 20261111; unit ids 24, 32, 35,
  52, 58, 62, 74, 76, 97, 108, 124, 135, as in `work/T6/C1-T6-key.json`).
- **Whisper + Google Translate item of a unit:** the raw faster-whisper words (the T6 faster-whisper
  transcript of the reference consensus, `work/T6/raw.whisper-large-v3.json`) whose timestamps fall
  inside the unit's time span, translated by Cloud Translation (Basic/v2, `en` -> `ru`,
  `format: text`). Mapping, fixed here: the unit spans are those of the pipeline's sentence units
  (`units.make_units` on the T6 run's `fixed.json`; the rebuilt English must equal the sampled
  English); a word belongs to the unit that contains its midpoint, and a word in a pause between two
  units goes to the nearer one (the boundary is the middle of the pause; before the first unit and
  after the last, the span is open), so a word is never lost or counted twice because the two engines
  time a boundary word slightly differently. The words' texts are joined with single spaces as
  faster-whisper wrote them (case and punctuation kept). The 12 texts go in one request; they are not
  taken from the full-fragment `.srt` run, whose units are cut from Whisper's own segments. The owner
  sees the **same English line** as for the other systems (the pipeline's corrected English, what the
  speaker said), so recognition errors count against the baseline, as they would for a viewer.
- **The owner's new rating file** (`work/T6/C1-T6-rating-2.md`, key `C1-T6-key-2.json`): the 12 Google
  Translate items + 4 **anchors**, items he already rated: 2 drawn from the 10 pipeline-only items and 2
  from the Gemini-only items of the other units (shared items excluded: their score counted for two
  systems), with `random.Random(20261113)` (`sample` on the item numbers in ascending order, pipeline
  first). The anchor shows exactly the text he rated before. The 16 items are shuffled with
  `random.Random(20261114)`, reshuffling the original order until no two items of the same unit are
  adjacent (as in the first file); single-rated 1–5, the same format and Russian instructions as the
  first file (the sentence about "two systems" says "different systems"). A Google Translate text equal
  to an already-rated text is still shown and gets its own score.
- **README translation column:** the owner's mean per system over the 12 units: pipeline and Gemini from
  the first rating (a shared item counts for both, as before), Google Translate from the new one. For
  each difference to the pipeline (pipeline − Gemini, pipeline − Google Translate): a paired bootstrap 95%
  interval, 10,000 resamples of the units, `random.Random(20261111)` (the first check's method and seed,
  so pipeline − Gemini reproduces +0.08 [−0.33, +0.50]). **Drift:** the mean absolute difference between
  the new and the old score of the 4 anchors (the signed mean is reported too). If it is **≥ 1.0**, the
  comparison with Google Translate is reported as "rated on a different day; drift ≥ 1 point" next to
  the numbers. The anchors' new scores do not replace the old ones.
- **Specification C1, Google Translate part** ("Claude's blind mean at least 1 point above Google
  Translate"): read off the means, met if pipeline mean − Google Translate mean ≥ 1.0 (point estimate,
  as in the criteria table, Spec criteria; the interval is reported next to it). Caveats stated with it: 12 units on
  T6 instead of 20 random fragments, the Google Translate items rated on another day. The VideoLingo part
  stays not run.
- **Gemini as a second rater** (`gemini-rater-v1`, informational only; **no rule uses it**): model
  `gemini-4-carbon` (`JUDGE_MODEL`), temperature 0, thinking at the model's default, one item per
  request, the owner's 1–5 instruction (in Russian, as he read it) with the same English line, a short
  reason and the score as structured output. It rates all 38 items (the first file's 22 and the new
  16, the anchors therefore twice) in an order shuffled with `random.Random(20261115)`; budget 0.30 USD
  (stops before crossing it). Output `work/T6/gemini-rater.json` (model ID, prompt version, item id,
  score, reason). Agreement is **not computed before the owner has rated**, and the scores go into no
  file he opens before rating. Reported afterwards, per system (an item's system as in the keys; a
  shared item counts for pipeline and Gemini) and over all 38 items: Spearman's rank correlation (average
  ranks for ties), mean absolute difference, share within ±1, and the mean difference rater − owner. Its
  rating of the Gemini-translated items is reported separately as the self-preference check: its mean
  difference to the owner on Gemini items against that on the other systems' items.
- **Scorer:** `uv run python scripts/translation_check.py score-baselines` after the file is filled;
  it writes the result below and the README column (markers in both files) and prints the same numbers.

**Prepared (2026-11-11).** Google Translate items: `work/baselines/google-translate/T6-units.json`
(12 units, 668 characters, part of the Google Translate run above). Rating file
`work/T6/C1-T6-rating-2.md` (16 items: 12 Google Translate, anchors = first-file items 1 and 5 (pipeline)
and 9 and 22 (Gemini), four different units; shuffled in 5 attempts; checked: the anchors' text equals the
first file's, no score shown) and key `C1-T6-key-2.json`, built by
`uv run python scripts/translation_check.py rating-2` (no API call). Gemini rater:
`uv run python scripts/translation_check.py rater`, 38 of 38 items, `gemini-4-carbon`, temperature 0,
**0.0785 USD**; scores in `work/T6/gemini-rater.json`, not looked at and not printed (the script prints
counts and cost only). Code: `talk_autopsy/translation_rating.py` (tests with fake ratings and a fake
rater); `judge.GeminiCaller` got an optional `temperature` (default unchanged).

<!-- translation-rating-result:start -->
**Result (2026-11-12).** The owner rated `work/T6/C1-T6-rating-2.md` (16 items) blind, one at a time; scored with `uv run python scripts/translation_check.py score-baselines`.

| 12 units, single-rated 1–5 | mean | scores 5 / 4 / 3 / 2 / 1 | pipeline − system, paired bootstrap 95% |
|---|---|---|---|
| Talk Autopsy (`pipeline-v3`) | **4.42** | 8 / 1 / 3 / 0 / 0 |  |
| Gemini translation (plain prompt) | **4.33** | 7 / 2 / 3 / 0 / 0 | +0.08 (−0.33 … +0.50) |
| Whisper + Google Translate | **4.42** | 6 / 5 / 1 / 0 / 0 | +0.00 (−0.42 … +0.42) |

- **Drift** on the 4 anchors (first-file item: old -> new score; item 1: 5 -> 5; item 5: 5 -> 5; item 9: 5 -> 5; item 22: 4 -> 4): mean absolute difference **0.00**, signed +0.00; below 1.0, so the two days are comparable by the rule.
- **Specification C1, Google Translate part** (pipeline mean at least 1 point above Google Translate): difference +0.00 (−0.42 … +0.42): **not met** (12 units on T6, not 20 fragments; Google Translate rated on another day). The VideoLingo part: not run, out of v1.0 scope.
- Google Translate translates raw Whisper; the owner saw the corrected English above it, so recognition errors count against it. Pipeline and Gemini scores are the first rating's.

**Gemini as a second rater** (`gemini-rater-v1`, informational; no rule uses it):

| items | n | Spearman | mean abs. difference | within ±1 | rater − owner |
|---|---|---|---|---|---|
| Talk Autopsy (`pipeline-v3`) | 14 | 0.12 | 1.00 | 71% | −0.57 |
| Gemini translation (plain prompt) (self) | 14 | −0.23 | 0.71 | 79% | +0.57 |
| Whisper + Google Translate | 12 | 0.43 | 0.92 | 75% | −0.58 |
| all items | 38 | 0.05 | 0.92 | 74% | −0.18 |

Self-preference check: rater − owner on Gemini-translated items +0.57, on the other systems' items −0.58 (mean of the two). A shared item counts for pipeline and Gemini; the anchors are rated twice by both.
<!-- translation-rating-result:end -->

**Reading (2026-11-12).** On random sentences of T6 the three systems were rated equal by one viewer
(4.42 / 4.42 / 4.33; all differences inside about ±0.4). Random sentence units of at least 6 words carry
few technical terms, so this rating says little about where the systems differ: term recognition on
accented speech (T2 term accuracy v2: pipeline 95.3% mean of 3 runs, raw Whisper 61.7%, the transcript
Google Translate translates; T6: 100% against 87.5% / 88.7%).

### Finding: LLM-as-rater self-preference (measured 2026-11-12)

Gemini (`gemini-rater-v1`, run before the owner's rating, scores held back until the owner had rated) rated
the same 38 items as the owner. Measured, not assumed:

- **No agreement with the owner:** Spearman 0.05 over all 38 items (per system 0.12, −0.23, 0.43; n 12–14);
  within ±1 point on 74%.
- **Self-preference:** the rater's mean on Gemini's own translations **4.93** (14 items) against **3.93**
  for the pipeline (14) and **3.83** for Google Translate (12); the owner's means on the same item
  groups are 4.36, 4.50 and 4.42. These owner means differ from the headline 4.33 / 4.42 / 4.42 because
  the item groups include the 4 anchors (items rated on both days, counted twice) and count each item
  shared by the pipeline and Gemini for both systems (14 = 10 own + 2 shared + 2 anchors). Rater − owner: +0.57 on Gemini items, −0.57 / −0.58 on the others. Raw means from
  `work/T6/gemini-rater.json` with the keys' system per item (a shared item counts for pipeline and Gemini).
- **Consequence:** the Gemini rater is **not usable** as a second rater of translations, least of all for
  comparing against a Gemini translation. Translation quality stays on the owner's single rating. The same
  bias was looked for in the audio judge ([Methodology: blind audio judge](#methodology-blind-audio-judge),
  share of spans where the judge chose the Gemini engine's text); here it is measured on ratings.

## Methodology: reference transcripts

WER and term accuracy are measured against a reference transcript per fragment.
The owner is not a native English speaker, so the reference is not proofread by
ear from one engine's draft. It is built by consensus of three independent
engines plus human adjudication of their disagreements only
([decisions.md](decisions.md), 2026-11-09):

1. Three engines transcribe the fragment without any glossary prompt:
   faster-whisper large-v3 (local, A1 (setup)), AssemblyAI Universal-3.5 Pro
   (A3 (setup)) and Gemini `gemini-3.8-flash` (A4 (setup)).
2. `talk-autopsy adjudicate` normalizes all three with the metrics tokenizer
   (fillers uh/um/er/ah/hmm removed, case and edge punctuation ignored) and aligns
   each cloud transcript to the faster-whisper backbone, the only one with
   measured word timings (`difflib.SequenceMatcher`, autojunk off). Words all
   three agree on are accepted. Disagreements become spans (merged when fewer
   than 3 agreed words apart): `majority` (two agree, pre-marked) or `split`
   (all differ, nothing pre-marked). Each listed span gets a clip of the
   backbone time range padded by 2.5 s each side.
3. The owner listens to the clips and ticks one variant per span or types the
   right text (`data/test-set/T<n>.adjudication.md`); `talk-autopsy resolve`
   composes the reference and a protocol of where every word came from.

| | T2 | T1 |
|---|---|---|
| backbone words (normalized) | 2035 | 1179 |
| unanimous words | 1796 (88.3%) | 1069 (90.7%) |
| majority spans | 89 | 62 |
| split spans | 25 | 11 |
| clip audio | 10.7 min | 6.9 min |
| majority spans where the odd one out is faster-whisper / AssemblyAI / Gemini | 25 / 45 / 19 | 53 / 4 / 5 |

The clip total exceeds the speech in dispute (1.2 and 0.8 min) because every
clip carries 5 s of padding; neighbouring clips overlap.

**Caveats.**

- **Bias in favour of A3 and A4.** AssemblyAI and Gemini helped create the
  reference, so their WER against it is biased in their favour: every word on
  which they agreed with one other engine was pre-marked as right. Their scores
  are not comparable with an engine that took no part (faster-whisper with a
  glossary prompt, VideoLingo, any later engine). The fairer comparison for
  those two is the protocol (`T<n>.ref.protocol.json`): how often the owner
  chose each engine's variant in the disputed spans.
- **A shared error wins the vote.** Where two engines make the same mistake the
  majority pre-mark is wrong. On T2 faster-whisper and AssemblyAI both wrote
  "Quadcode" for "Claude Code" in 7 pre-marked spans. Only the owner's listening
  catches these, which is why the majority blocks are skimmed, not trusted.
- **Unanimous errors stay.** A word all three engines got wrong in the same way
  is accepted without review. Its rate is unknown; it lowers every engine's WER
  equally.
- faster-whisper splits some written words into pieces (`up` + `-level`); the
  alignment joins them back to the words of the segment text (13 joins on T2,
  12 on T1). The T1 backbone still holds the two `.`-only segments from A1 (setup)
  (the engine has dropped such segments since 4f378a5); they normalize to
  nothing and do not enter the alignment.

### Who decided each reference

Decided by, per fragment (added 2026-11-11 after independent LLM review #1, findings
2 and 11: no human listener decided T1, and a Claude model must not be read
as an independent check of the Claude stages):

| Fragment | Disputed spans | Decided by | Spans |
|---|---|---|---|
| T2 | 114 | owner (by ear; 2 of them "not sure", then majority or grammar) | 19 |
| | | rule (product name `Claude Code` for "Quadcode") | 20 |
| | | rule (reference conventions, glossary spelling) | about 30 |
| | | engine majority | the rest (about 45) |
| T1 | 73 | assistant (Claude), without listening: product and model names 39, majority 26, meaning or convention 8 | 73 |
| T6 | 86 | judge (Gemini, `audio-judge-v1`; failed its T2 calibration) in reference J; in reference M the engine majority for the 65 majority spans, the judge for the 21 split spans | 86 |

Words all three engines agreed on are accepted without a decision (engine
agreement): T2 88.3%, T1 90.7%, T6 92.8% of the backbone words.

The two references were not decided the same way, and that changes what their
numbers can carry.

- **T2** (non-native speaker, `T2.ref.txt`, 2052 words): 114 spans. 19 were decided
  by the owner listening to the clip (2 of them marked "not sure" and resolved
  by majority or grammar); 20 by the product-name rule (`Quadcode` /
  `QuadCode` -> `Claude Code`, the product on the slides); about 30 by the
  reference conventions and the glossary (slash commands, digits, `macOS`,
  `WebFetch`, `LLM`, compound spelling); the rest by engine majority. The owner
  noticed that a variant read before listening biases what one hears; one span
  was re-listened blind and changed (`them` -> `Vim`).
- **T1** (native speaker, `T1.ref.txt`, 1205 words): 73 spans, **no human
  listening**. The assistant (a Claude model) decided them from rules, context
  and majority: 39 product and model names, 26 by majority, 8 by meaning or
  convention. A 20-word Whisper hallucination at 02:01 was removed (absent from
  both cloud transcripts); `Excalibur` -> `Excalidraw` twice.

Consequence: T1's reference was adjudicated by a Claude model, so in stage 2
(Claude correction) T1 is secondary evidence: a Claude correction may agree
with it for the same reasons the adjudicator did. T2 (human-checked where it
mattered) and the sealed T6 carry the weight.

Share of disputed spans whose decided text matched each engine's variant
(`T<n>.ref.protocol.json`, `spans_by_engine`; a span can match more than one
engine, so the shares do not add up to 100%). This is the fairer comparison
for AssemblyAI and Gemini, whose WER is biased in their favour:

| | T2 (114 spans, human-checked) | T1 (73 spans, assistant-decided) |
|---|---|---|
| Gemini | 71 (62%) | 65 (89%) |
| faster-whisper | 52 (46%) | 6 (8%) |
| AssemblyAI | 30 (26%) | 60 (82%) |
| typed (no engine had it) | 29 (25%) | 1 (1%) |

## Methodology: blind audio judge

Written 2026-11-10, before any judge result on T2 or T6.

**Why.** The owner (not a native speaker) has withdrawn from adjudicating
reference spans: not an expert, and reading the variants before
listening biases what one hears (T2 span `them` -> `Vim`). The assistant
cannot adjudicate either: it cannot hear audio, and Claude is the corrector
the T6 run scores. Spans are now decided by a blind audio judge.

**Method** (`talk_autopsy/judge.py`, `audio-judge-v1`; evaluation tooling,
not part of the frozen pipeline). Gemini `gemini-3.8-flash` (the most capable
free-tier model on 2026-11-10; `gemini-4-argon` is paid only; *the T2 run used
`gemini-4-carbon` on the paid tier, see below*), one request per
majority or split span with the span's clip inline. The structured answer has
three fields in this order: `heard`, a verbatim transcription of the whole
clip written before the options; `choice`, the number of the matching option
or `none`; `final_text`, the marked part as it should be written. The prompt
asks for the transcription before it lists any option. The options are the
engines' variants, deduplicated by their normalized tokens, shuffled with a
per-span seed and numbered with no engine names or vote counts. The prompt
says that every option may be wrong and that agreeing with the majority is
not a goal. The backbone's context words before and after the span locate the
marked part. The reference conventions of `data/test-set/README.md` are given
as writing rules; `final_text` is cleaned by the rules applied in code
(fillers removed). No glossary is given, on T2 or T6. Output: `work/<ID>/judge.jsonl`
(resumable span by span) and `data/test-set/<ID>.adjudication.judged.md`
(the judge's ticks; the original file is not changed), resolved with
`talk-autopsy resolve <ID> --adjudication <judged file>`.

Measured per run: the share of spans where the judge chose the variant that
came from the Gemini engine (self-preference: the judge is a Gemini model
too), the share of `none`, and the share where `heard` already contains the
chosen text.

**Calibration on T2** (all 114 spans; `talk-autopsy judge-compare T2`). A span
agrees when the judge's text and the decided text are equal after the metrics
normalization. Agreement is reported overall, by verdict (89 majority, 25
split) and by how the span was decided. The owner's list of the 19 spans he
decided by ear was not recorded (only counts were recorded at the time), so the classes
are inferred from each decision's pattern (`judge.classify_decision`, with
the T2 glossary): **majority** 74 (decided with the pre-marked text),
**rule** 32 (the product-name rule, a spelling convention, or a glossary
term), **ear** 8 (a majority overruled, or a split decided, with other words
and no rule). The 19 by-ear decisions are therefore approximated by these 8
spans; the other 11 sit among the majority or rule classes and cannot be
told apart. *(The list was recorded afterwards, `work/T2/owner-by-ear.json`;
the T2 run below reports those 19 spans directly.)* Also reported: the WER between `T2.ref.txt` and a reference
resolved from the judged file (into a temporary path; `T2.ref.txt` is not
changed), and the judge's Gemini share next to the owner's on the same spans
(71 of 114, 62%, `T2.ref.protocol.json`).

**Decision rule.** The judge is accepted for T6 if, on T2, overall agreement
is at least 90% and the WER between the two T2 references is at most 2%.
Otherwise the disagreement pattern is logged here and T6 is not judged.

**Free tier, 2026-11-10 (first attempt): not run.** The free tier allows 20
requests per day per project for `gemini-3.8-flash` (HTTP 429, quota
`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, value 20; the docs do
not publish it), and it was used up before the first T2 span was judged. The
owner then moved the project to the paid tier (decisions, 2026-11-10).

### Calibration on T2, paid tier (2026-11-10)

- **Method in two sentences.** Gemini listens to each disputed span's clip,
  writes down what it hears before it sees any option, then picks one of the
  engines' variants (deduplicated, shuffled, unlabeled) or types its own text.
  Its decisions are compared with the decided T2 adjudication, span by span,
  after the metrics normalization.
- **Model and settings.** `gemini-4-carbon` (paid Standard tier; not the STT
  engine's model), thinking level `low` (the lowest the model allows; it used 0
  thinking tokens on every span), pace 2 s, the prompt and options exactly as
  in the free-tier attempt (`audio-judge-v1`). The newest Pro model was the first choice:
  `gemini-3.1-pro-preview` answers 404 for this key and `gemini-2.5-pro` routes
  to the STT engine's model from 2026-12-01, so the judge is the pricing page's
  "most capable reasoning model". All 114 spans judged by this one model; no
  free-tier pilot records existed.
- **The owner's 19.** The owner's list of the spans decided by ear was recorded
  after the free-tier attempt (`work/T2/owner-by-ear.json`, 19 span ids); `judge-compare` now
  uses it as its own class (`owner_ear`) and infers the class of the other
  spans as before.

| Agreement with the decided T2 text | spans | agree | share |
|---|---|---|---|
| **overall** | 114 | 90 | **79.0%** |
| decided by the owner's ear (recorded list) | 19 | 13 | 68.4% |
| decided by rule (inferred: product name, convention, glossary) | 30 | 23 | 76.7% |
| decided with the majority pre-mark (inferred) | 60 | 51 | 85.0% |
| other, by ear (inferred, not on the owner's list) | 5 | 3 | 60.0% |
| majority spans (verdict) | 89 | 75 | 84.3% |
| split spans (verdict) | 25 | 15 | 60.0% |

- **WER between the two T2 references** (`T2.ref.txt` against a reference
  resolved from `T2.adjudication.judged.md` into a temporary path):
  **1.90%** (2052 reference words; 11 substitutions, 2 deletions, 26
  insertions). `T2.ref.txt` is unchanged.
- **Judge indicators.** It chose the Gemini engine's variant in 85 of 114
  spans (74.6%; the owner's decisions matched it in 71, 62%); `none` (own
  text) in 15 (13.2%); `heard` contains the chosen text in 110 of 113 (97.4%);
  `heard` written before `choice` in 114 of 114.
- **Cost and time.** $0.2934 for 114 spans ($0.0026 per span; 110,383 input,
  7,260 output, 0 thinking tokens), by the run's own counter at list price;
  8.9 s mean latency, 17 min for the run, no retries.
- **Disagreements on the owner's 19** (6): in 4 the judge kept a repetition
  or false start the reference leaves out (0005, 0022 where the owner chose no
  words, 0028, 0104); 0020 (owner not sure) adds two words and writes a
  compound as one word; 0015 the judge chose a contraction where the owner
  heard the two full words.
- **Disagreement pattern (all 24).** 14 are words the judge adds to the
  decided text: repetitions, false starts and short words around the span
  (a doubled word, a doubled two-word phrase, a short opener, a restart); the judge's
  writing rules ask for every spoken word including repetitions, while the
  T2 reference mostly follows the engines, which drop them. 5 are spelling
  conventions the reference applies and the judge does not (`Shift+Enter`,
  `at-mention`, `Command+F`, `stand-up`, `3,000`). 5 are one-word
  substitutions (a contraction, a verb, a tense, an article, a determiner).
  Which side is right in the first group is not
  known: the reference was never checked for dropped repetitions.
- **Decision (rule fixed before the run).** Agreement 79.0% is below 90%
  (WER 1.90% is within 2%), so **the judge is not accepted for T6** and T6 is
  not judged. The rule and the prompt are not changed after seeing these
  numbers; a revised judge (for example a writing rule on repetitions that
  matches the reference) would be a new version (`audio-judge-v2`) and would
  need a new calibration before T6.

### Post-hoc rescoring (2026-11-10)

**This is a post-hoc change made after seeing the calibration result; the
threshold was not changed and no new judge requests were made.**

- **The calibration's numbers, as they were:** agreement 79.0% (90 of 114), the
  owner's 19 by ear 13 (68.4%), WER between the two T2 references 1.90%:
  failed the rule (agreement >= 90% and WER <= 2%).
- **Why a rescoring.** 14 of the 24 disagreements were repetitions and false
  starts (a doubled word, a repeated three-word phrase) that the judge writes, as its
  prompt asks, and the T2 reference mostly omits; 5 more were formatting
  (`Shift+Enter`, `3,000`). The reference convention on repetitions was never
  written down, and the comparison did not normalise formatting.
- **The rule added** (`data/test-set/README.md`, reference conventions,
  marked "added 2026-11-10 after the judge calibration"): immediate
  repetitions and restarts are written once; numbers without thousands
  separators; key combinations as `Shift+Enter`. In code,
  `judge.normalize_for_adjudication` canonicalises key combinations, removes
  thousands separators, collapses an immediately repeated sequence of 1-4
  words (case and punctuation ignored) until stable, then applies the
  existing reference conventions in code (fillers). It is applied identically
  to both sides of every comparison and to the judged spans' texts when a
  reference is resolved from a judged file (`resolve --normalize-adjudication`);
  for the WER both whole references pass through it. The metrics normaliser
  (`metrics.tokenize`), `benchmark.csv` and `T2.ref.txt` are unchanged. The
  new README rows are not passed to the judge's prompt (`read_conventions`
  skips them), so `audio-judge-v1` stays exactly as calibrated.
- **Rescored** (`talk-autopsy judge-compare T2 --normalize-adjudication`, from
  the existing `work/T2/judge.jsonl`; `work/T2/judge.compare.normalized.json`):

| Agreement with the decided T2 text | spans | calibration | rescored |
|---|---|---|---|
| **overall** | 114 | 90 (79.0%) | **94 (82.5%)** |
| decided by the owner's ear (recorded list) | 19 | 13 (68.4%) | 14 (73.7%) |
| decided by rule (inferred) | 30 | 23 (76.7%) | 26 (86.7%) |
| decided with the majority pre-mark (inferred) | 60 | 51 (85.0%) | 51 (85.0%) |
| other, by ear (inferred) | 5 | 3 (60.0%) | 3 (60.0%) |
| majority spans (verdict) | 89 | 75 (84.3%) | 76 (85.4%) |
| split spans (verdict) | 25 | 15 (60.0%) | 18 (72.0%) |

- **WER between the two T2 references** after the normaliser on both:
  **1.17%** (2043 words; 8 substitutions, 2 deletions, 14 insertions).
- **Self-preference** is unchanged (the same choices): the judge chose the
  Gemini engine's variant in 85 of 114 spans (74.6%; the owner's decisions 71,
  62%); `none` 15 (13.2%).
- **Decision (rule unchanged): failed.** 82.5% is below 90% (WER 1.17% is
  within 2%), so the judge is still not accepted for T6; T6 is not judged, and
  no T6 reference or glossary exists.

**The 20 remaining disagreements, by class** (`term`: a differing token
belongs to a T2 glossary term; `formatting`: spelling only; `word`: other
words): term 2, word 18, formatting 0.

| Span | Class | How the judge's text differs (no transcript text quoted) | Pattern |
|---|---|---|---|
| 0032 | term | another spelling of `at-mention` | spelling of a glossary term |
| 0082 | term | `stand-up` written as one word | spelling of a glossary term |
| 0020 | word | two words repeated across the span edge; a compound written as one word | repetition across the span edge |
| 0022 | word | four words where the reference has none (a three-word phrase repeated) | repetition across the span edge |
| 0028 | word | one word, doubled across the edge | repetition across the span edge |
| 0033 | word | one word where the reference has none, doubled across the edge | repetition across the span edge |
| 0073 | word | three words where the reference has none (a restart) | restart across the span edge |
| 0093 | word | one word where the reference has none, doubled across the edge | repetition across the span edge |
| 0104 | word | a three-word restart (one of its words is a context word) | restart across the span edge |
| 0046 | word | four more words of a restart with other words | restart with other words |
| 0084 | word | a restart with a contraction | restart with another word |
| 0007 | word | one word after the product name | word the reference omits |
| 0043 | word | one word after the product name | word the reference omits |
| 0062 | word | one word before the product name | word the reference omits |
| 0081 | word | one opening word | word the reference omits |
| 0015 | word | a contraction for two words | substitution |
| 0038 | word | another verb | substitution |
| 0048 | word | another tense | substitution |
| 0092 | word | another article before an acronym | substitution |
| 0106 | word | another determiner | substitution |

Pattern: 7 are repetitions or restarts that the span-level rule cannot see,
because one copy sits in the context words outside the span; 2 more are
restarts with changed words; 4 are words the judge hears and the reference
omits; 5 are one-word substitutions; 2 are spellings of glossary terms. As an
illustration only (not a decision input: the rule above is span-level, as
specified), applying the normaliser to the context words plus the span on
both sides lifts 3 more spans (0028, 0033, 0104) to agreement: 97 of 114,
85.1%, still below 90%. The judge's own transcriptions (`heard`) show that
it writes what it hears more completely than the reference does; whether the
reference or the judge is right on the omitted words and the substitutions is
not known without a listener.

## T6 protocol

Written 2026-11-11, before the T6 run, after independent LLM review #1. It replaces the
earlier versions of the protocol, kept below as history. No T6 content is
recorded here: counts, durations and costs only.

**What T6 is, truthfully.** A 10-minute fragment cut blind (by speech density,
`data/test-set/README.md`) from a 41-minute workshop with a live demo. The
owner chose the source from candidates by type (non-native speaker, technical
workshop); its topic, AI-assisted software development, is close to T1 and
T2 and is known. The owner has not watched it. Three engines transcribed it
to build the reference; the blind Gemini audio judge decided the disputed
spans (it failed its T2 calibration, hence two references and bounds); Gemini
extracted the glossary (51 terms). The pipeline was never run on it and no
prompt or setting was tuned on it; the prompts' examples were only checked by
code not to occur in any test-set glossary or reference, T6's included. **No human has checked the reference**: no
fluent listener is available (owner's decision); this is a limitation, not
fixed.

**Run.** `talk-autopsy run` from a clean `git worktree` of tag `pipeline-v3`
(commit `d180a73ddc3c95f2b7cb79e8621e541aa36f99b5`; Freeze pipeline-v3 below),
the commit hash recorded with the run; auto-glossary only (no hand glossary,
no context); one run; the settings in `.env` that select models or versions
empty. Then `talk-autopsy eval T6 --hyp <worktree>/work/<name>/fixed.json
--raw <worktree>/work/<name>/raw2.json --record --engine pipeline-v3 --stage
fix` from the main checkout.

**Metric.** Term accuracy v2 (Metrics above: in place, possessives accepted)
of the corrected English against reference J and reference M, the lower and
the upper bound over the occurrences inside uncertain spans
(`metrics.bounded_term_accuracy`, now on v2), and the **Wilson 95% interval**
of each. Counts on T6 under v2: **72 term occurrences** of 51 terms in J, **6
inside uncertain spans**; the most frequent term has 5 of the 72, so no term
dominates. Also reported, not used by the verdict: WER against J and M, macro
and without-top averages, precision, the false-edit rate.

**Verdict** (threshold 85%, `metrics.gate_verdict`):

- *Pass (demonstrated):* the Wilson lower limit of the lower bound >= 85%.
- *Pass (not demonstrated):* the lower bound >= 85% but its Wilson lower limit
  < 85%.
- *Fail:* the upper bound < 85%.
- *Inconclusive:* otherwise.
- *Overfitting signal:* T6 (reference J, micro) more than 10 points below the
  mean of the three `pipeline-v3` repeats on T2 (Freeze pipeline-v3, Repeats:
  mean 95.33%, so T6 below 85.33%) is reported as a separate finding regardless of the verdict
  (`metrics.overfitting_signal`; `eval T6` prints it).

**What 72 occurrences can show.** With n = 72 the Wilson lower limit reaches
85% only from 68 hits (94.4%; 67 hits give 84.8%). The lower bound counts the
6 uncertain occurrences as misses, so it is at most 66 / 72 = 91.7%, whose
Wilson lower limit is 83.0%. **"Pass (demonstrated)" therefore cannot be
reached on T6**: the best possible verdict is "pass (not demonstrated)". This
was computed before the run and is stated so that the result is not
over-read.

**Scope.** A pass shows transfer to an unseen speaker and recording on a
related topic. It does not show transfer to a new domain; that is T3's job,
after T6.

**Translation check on T6 (pre-registered; amended twice before the run,
below).** After the run, **12 sentence units with at least 6 English words**
are drawn at random from T6's units (`talk_autopsy/translation_check.py`,
`scripts/translation_check.py sample`; `random.Random(20261111)`, seed recorded
here, the qualifying units in id order; the English of each unit rebuilt from
the run's `fixed.json`). Baseline: the same English units translated by Gemini
(`gemini-4-carbon`, paid tier, one request per unit, plain prompt "Translate
this English sentence from a technical talk into natural Russian for
subtitles. Answer with the translation only."). The owner rates **at most 24
items shuffled together** (seed 20261112), one at a time, single-rated 1–5,
without knowing which system wrote which; the English is shown above each item
(as in C1; caveat: the rater reads the source first). **Rule: absolute
translation quality on T6 is met if the pipeline's mean >= 4.0** (called "C1
on T6" when written; it is not the specification's C1, which compares against
Google Translate). The difference to the baseline is reported
with a paired bootstrap 95% interval (10,000 resamples of the 12 units); no
other conclusion is drawn. Implemented and tested with a fake client on
2026-11-11; prepared after the run, not rated yet (below). (As first written:
20 units of any length, 40 items.)

*Amendment (2026-11-11, before the T6 run).* The owner asked that
the two translations of one unit are never adjacent in the rating file. The
order under seed 20261112 depends only on the seed, not on any text. Its
first shuffle puts two units' items side by side (positions 11–12 and 30–31),
so the rating file now takes the first shuffle without adjacent pairs from
the same generator (`random.Random(20261112)`, each attempt shuffling the
original order; for 40 items the second attempt has none). The number of shuffles is
written into the key. The rating file's instructions are in Russian, and it
is written to `work/T6/C1-T6-rating.md` with the key in
`work/T6/C1-T6-key.json`. The sample (seed 20261111), the baseline prompt,
the rule and the scorer are unchanged.

*Amendment 2 (owner's decision, 2026-11-11 20:00, before the T6 run; the
pipeline had not started).* The check is reduced: **12 units instead of 20**,
same seed (20261111), drawn only from sentence units with **at least 6 English
words** (counted by `metrics.tokenize`, the metrics' one normalization, so
fillers do not count). If the pipeline's and Gemini's translations of a unit
are **identical after whitespace and punctuation normalisation**
(`translation_check.normalized`: Unicode punctuation removed, whitespace
collapsed, case kept), the item is shown once (marked `shared` in the key,
with the list `shared_units`) and its score counts for both systems. So the
rating file has at most 24 numbered items and the Gemini baseline covers the
12 units only. The no-adjacent-pairs rule of the first amendment still
applies; with fewer items the number of shuffles it needs is whatever the key
records. Everything else is unchanged: single rating, shuffled, the rule
(pipeline mean >= 4.0) and the baseline difference with a paired bootstrap
interval over the units.

*Prepared 2026-11-11, after the run (not rated, not scored).*
`scripts/translation_check.py sample --fixed work/T6/run-pipeline-v3/fixed.json
--translated work/T6/run-pipeline-v3/ru.json`: 12 of 145 units drawn (seed
20261111; units 24, 32, 35, 52, 58, 62, 74, 76, 97, 108, 124, 135; each with at
least 6 words). Baseline: `gemini-4-carbon` (paid tier, the plain prompt above,
one request per unit), 12 units, **0.0240 USD**. Rating file
`work/T6/C1-T6-rating.md`: **22 items** (10 pipeline, 10 baseline, 2 shared:
units 32 and 52 have identical translations after normalisation), shuffled
with seed 20261112, 4 shuffles to the first order without adjacent pairs;
key `work/T6/C1-T6-key.json` (system per item, `shared_units`). Rated by the
owner and scored on 2026-11-11: [T6 translation check result](#t6-translation-check-result-2026-11-11).

### History of the protocol (superseded 2026-11-11)

The first protocol (2026-11-09) and its changes, as written then:

T6 is the sealed hold-out of stage 2 (a workshop with a live demo, a
non-native speaker). Its 10-minute window was cut blind on
2026-11-09 by speech density only (`data/test-set/README.md`): 25:46–35:46 of
41:24, 93.8% non-silent. Nothing else about it is known to the project.

1. **Sealed until the freeze.** Nobody (the owner, the assistant, any prompt
   or log) reads, transcribes or describes T6's content until `correction-v2`
   (or its successor) and `translation-v2` (or its successor) are frozen with
   a git tag. Before that, the only T6 facts allowed anywhere are durations and
   silence statistics.
2. **Then the reference.** After the tag, T6's reference is built as T1's and
   T2's were (three engines, `adjudicate`, `resolve`), with one difference: the
   owner adjudicates every span, the assistant decides none (T1's
   assistant-decided reference is the reason). Its glossary is made the same
   way as T2's, by the owner.
   *Changed 2026-11-10:* the owner has withdrawn from
   adjudication; the spans are decided by the blind audio judge if it passes its
   T2 calibration (Methodology: blind audio judge), and the glossary is drafted
   by Gemini from the reference (`talk-autopsy glossary-ref`), not by Claude.
   *Changed 2026-11-10 (owner's decision, after the judge failed its
   calibration):* the judge decides the spans anyway, but the reference is
   built in two variants and the gate uses a lower and an upper bound, so the
   conclusion does not depend on whether the judge is right where it alone
   decides (decisions; "Reference and gate with bounds" below).
3. **One run.** The frozen pipeline (`talk-autopsy run` with the tagged code,
   the auto-glossary and no hand glossary) runs on T6 once. Its numbers are
   recorded whatever they are; a prompt change after seeing them starts a new
   version that T6 can no longer judge.

Engines run and adjudication file prepared on 2026-11-10; owner adjudication
pending. After the `pipeline-v2` tag: faster-whisper large-v3 (local, 8
threads, backbone), AssemblyAI `universal-3-5-pro` without key terms (0.035
USD), Gemini without a glossary. Gemini `gemini-3.8-flash` answered 503
UNAVAILABLE ("high demand") on three attempts, so the third engine is the
configured fallback model `gemini-3.6-flash` (free tier), noted at the top of
`data/test-set/T6.adjudication.md`. `talk-autopsy adjudicate T6`: 2037
backbone words, 92.8% unanimous, 65 majority and 21 split spans, 86 clips
(7.8 min) in `work/T6/clips/`. No reference, glossary or pipeline run yet;
nothing from the content is recorded outside `data/test-set/` and `work/T6/`.
2026-11-10 (paid tier): the blind audio judge failed its T2 calibration
(agreement 79.0% against the 90% rule), so T6 was not judged: 0 of 86 spans,
no reference, no glossary.
2026-11-10 (post-hoc): rescored on T2 with the adjudication
normaliser (Post-hoc rescoring above): 82.5%, still below 90%, so T6 is
still not judged; no Gemini request was made.

#### Reference and gate with bounds (2026-11-11)

*Superseded the same day by the protocol above: the metric is now term
accuracy v2, the verdict uses the Wilson interval, and the run uses
`pipeline-v3`, not `pipeline-v2`. The reference, the uncertain spans and the
glossary described here are unchanged.*

Reference built 2026-11-11 by the blind audio judge (`gemini-4-carbon`,
thinking `low`, paid tier; `audio-judge-v1` exactly as calibrated) in two
variants (judge everywhere / engine majority where it exists); 86 spans
judged, 50 uncertain spans containing 6 glossary-term occurrences; glossary
of 51 terms extracted by Gemini. Gate: pass if the lower bound of term
accuracy ≥ 85%, fail if the upper bound < 85%, otherwise inconclusive.

- **The two references** (`talk-autopsy judge-refs T6`, `talk_autopsy/ref_bounds.py`):
  **J** (`T6.ref.txt`): every judged span takes the judge's `final_text`;
  **M** (`T6.ref.majority.txt`): majority spans take the engines' majority
  variant, split spans the judge's `final_text`. Both pass every judged
  span's text through the adjudication normaliser
  (`judge.normalize_for_adjudication`). **Uncertain spans**
  (`work/T6/uncertain_spans.json`: span numbers, verdicts, character and
  word ranges in both references, the SHA-256 of both files; no text): the
  split spans (decided by the judge alone) plus the majority spans where J
  and M differ (tokens compared with case kept).
- **Counts.** Spans judged: 65 majority, 21 split. The judge overrode the
  majority in 29 of the 65 majority spans, so 50 spans are uncertain (21 +
  29). In 1 span the judge's `final_text` differs from the option it chose
  (J uses `final_text`, as specified). Words: J 2084, M 2043; WER of M
  against J **2.45%** (6 substitutions, 43 deletions, 2 insertions). Judge
  indicators: the Gemini engine's variant chosen in 48 of 86 spans (55.8%),
  `none` (own text) in 18 (20.9%), `heard` contains the chosen text in 79 of
  81 (97.5%), `heard` written before `choice` in 86 of 86.
- **Glossary** (`talk-autopsy glossary-ref T6`, `ref-glossary-v1`, same
  model, from J): 52 proposed, 1 dropped as absent from J, **51 kept**; every
  kept term occurs in J (checked in code). 72 term occurrences in J, **6**
  inside uncertain spans. `T6.glossary.md` stays local (git-ignored) until
  the T6 run is recorded.
- **Cost and time.** Judge $0.2206 (86 spans, $0.0026 per span, 10 min, no
  failed spans); glossary $0.0226; Gemini total **$0.2432** by the tools'
  own counters at list price. Claude API and AssemblyAI: none.
- **The gate** (`metrics.bounded_term_accuracy`, used by `talk-autopsy eval
  T6`): term accuracy against J and against M; per term with `c` certain and
  `u` uncertain occurrences in J and `h` in the hypothesis, the certain ones
  are scored as usual (position-free, min(c, h)); **lower bound** = uncertain
  occurrences counted as misses, **upper bound** = counted as hits, so lower
  <= accuracy against J <= upper. With 6 of 72 occurrences uncertain the two
  bounds are at most 8.3 points apart. Verdict: pass if lower >= 85%, fail
  if upper < 85%, otherwise inconclusive; all four numbers (J, M, lower,
  upper) are reported. `eval` on T6 prints counts only (no term rows) and
  refuses `uncertain_spans.json` built for other reference files.
- **Known limit.** A term the engines' majority has inside a span where J
  has other words is not counted by the bounds (the glossary and the
  occurrences come from J); the accuracy against M shows that case.
- **Next (as planned then):** the frozen `pipeline-v2` on T6 once, after the
  independent LLM review, then `eval T6 --record`.

## T6 result (2026-11-11)

The single run of the hold-out under the T6 protocol above (written before
the run). One run, never repeated; nothing was tuned, re-run or re-scored
after the numbers were seen.

- **Run:** `talk-autopsy run data/test-set/T6.wav --lang ru --name T6-v3
  --max-cost 1.0` from a clean `git worktree` of tag `pipeline-v3`, commit
  **`d180a73ddc3c95f2b7cb79e8621e541aa36f99b5`** (status clean, hash checked),
  keys from the main checkout's `.env` by path; fresh work folder (no cache);
  auto-glossary only (32 terms, all 32 sent as key terms, none dropped), no
  hand glossary, no `--domain`, no context; `.env` and shell settings that
  select a model or a version empty; translation `translation-v2.1`. Started
  20:06:46, ended 20:08:56, exit 0, no fallback. `--max-cost 1.0` is the
  Claude budget of that work as a stop; the estimate was 0.21 USD. Outputs in
  `work/T6/run-pipeline-v3/` (git-ignored); the worktree was removed.
  Scored from the main checkout: `eval T6 --hyp <run>/fixed.json --raw
  <run>/raw2.json --record --engine pipeline-v3 --stage fix`.

| | value | Wilson 95% |
|---|---|---|
| term accuracy v2 against J (micro) | **100.00%** (72/72, 51 terms) | 94.9–100.0% |
| term accuracy v2 against M (micro) | **100.00%** (71/71, 50 terms) | 94.9–100.0% |
| lower bound (6 uncertain occurrences counted as misses) | **91.67%** (66/72) | 83.0–96.1% |
| upper bound (counted as hits) | **100.00%** (72/72) | 94.9–100.0% |

- **Verdict** (threshold 85%): **pass (not demonstrated)**: the lower bound
  is >= 85% but its Wilson lower limit, 83.0%, is < 85%. This is the best
  verdict T6 can give (computed before the run, protocol above).
- **Overfitting signal: no.** T6 (J, micro) 100.00% is not below 85.33% (10
  points under the T2 mean of the three `pipeline-v3` repeats, 95.33%).
- **Scope** (from the protocol): a pass shows transfer to an unseen speaker
  and recording on a related topic. It does not show transfer to a new
  domain; that is T3's job, after T6.
- **Secondary numbers** (not used by the verdict): macro 100.00% (J and M);
  without the top term 100.00% (J and M); term precision 100.00% against J,
  98.61% against M (1 occurrence in place in J but not in M); WER 2.89–4.70%
  (M 2.89%: S 14, D 41, I 4 of 2043 words; J 4.70%: S 14, D 81, I 3 of 2084
  words); correction: 1 word edit in 1 of 145 segments (`genetic` ->
  `agentic`), 0 false edits against J and M (false-edit rate 0%, 1 helpful
  edit); reading speed of the Russian subtitles: 177 cues, 52.5% above 17
  cps, 16.9% above 25 cps (the cost report rounds it to 17.0%), max 49.8 cps,
  22 cues shorter than 1 s, 0 longer than 7 s.
- **Cost and time per step** (wall clock; list price): stt 21.8 s, 0.0350 USD
  AssemblyAI; glossary 19.3 s, 0.0027 USD Claude (Haiku); stt2 18.4 s, 0.0433
  USD AssemblyAI; align 0.1 s; fix 25.6 s, 0.0645 USD; translate 40.9 s,
  0.0728 USD; srt 0.4 s. Total 126.6 s; **0.1400 USD Claude, 0.0783 USD
  AssemblyAI** (free credit).
- **Missed term occurrences:** none against J (0 of 72) and none against M (0
  of 71), so the classified list (recognition error / spelling convention /
  reference doubt) is empty. The 6 occurrences inside uncertain spans are all
  in place against J.

**What this does and does not show.** It shows that the frozen `pipeline-v3`,
run once on a 10-minute fragment it never saw, puts every glossary term of
both references in place, and that the result is not below the T2 repeats
(no overfitting signal). It does not demonstrate a pass at 85%: 72
occurrences cannot, and the verdict is "pass (not demonstrated)". It does not
show transfer to another domain (one topic in the whole test set). The
references were not checked by a human; they come from three engines,
AssemblyAI among them (the pipeline's own engine, so its numbers are biased in
its favour, as on T1 and T2), and a Gemini audio judge that failed its
calibration; the glossary was drafted by Gemini from reference J, so it holds
only terms J contains. Correction changed one word, so these numbers measure
mostly AssemblyAI; the key terms changed no term occurrence on T6 (Key-term
ablation on T6, 2026-11-12). Translation quality on
T6 is the separate pre-registered check below.

## Key-term ablation on T6 (2026-11-12)

No new run and no API call: existing T6 transcripts scored with `eval T6`
(term accuracy v2 against J and M, bounds), to see what the key terms do on
the hold-out (independent LLM review #2 asked for the ablation on T6, not only
on T2).

| T6, against J (M) | WER | micro | without the top term | macro | precision |
|---|---|---|---|---|---|
| AssemblyAI, no key terms (the transcript of the reference consensus, 2026-11-10) | 4.56% (3.13%) | 98.61%, 71/72 (98.59%, 70/71) | 100.00% | 99.61% | 100.00% (98.59%) |
| the `pipeline-v3` run's first pass (`raw.json`, no key terms) | 4.56% (3.13%) | 98.61%, 71/72 (98.59%, 70/71) | 100.00% | 99.61% | 100.00% (98.59%) |
| its second pass (`raw2.json`, 32 auto-glossary key terms) | 4.75% (2.94%) | 98.61%, 71/72 (98.59%, 70/71) | 100.00% | 99.61% | 100.00% (98.59%) |
| after correction (`fixed.json`, the T6 result) | 4.70% (2.89%) | 100.00%, 72/72 (71/71) | 100.00% | 100.00% | 100.00% (98.61%) |

Lower and upper bounds are 91.67% and 100.00% in every row (the 6 uncertain
occurrences are in place in all four). Benchmark row: `assemblyai`, stage
`stt`, 2026-11-12 (the first row of the table).

- **Reading.** On T6 the key terms changed no term occurrence: the
  recogniser alone had 71 of 72. The one miss, "agentic engineering" written
  as another word, stayed through the second pass and was fixed by
  correction's single edit (`genetic` -> `agentic`). On T2 the key terms do
  the work (80.4% -> 96.3% with the B5 auto-glossary, 94.4–96.3% in the
  `pipeline-v3` repeats). So "the key terms do the work" holds on the
  development fragment, not on the hold-out, where the speech is easier for
  the recogniser (T6 raw Whisper 87.5% against 61.7% on T2).
- **Bias.** The no-key-term transcript was one of the three engines that
  built both T6 references (AssemblyAI is also the pipeline's own engine), so
  its numbers are biased in its favour, as on T2.
- **Not changed:** no prompt, setting or rule; the T6 verdict stands.

## T6 translation check result (2026-11-11)

The pre-registered check of the T6 protocol (12 units, seed 20261111; Gemini
baseline; 22 items shuffled with seed 20261112, 2 of them shared). The owner
rated `work/T6/C1-T6-rating.md` blind, one item at a time, without having
watched T6; the scorer was then run once:
`uv run python scripts/translation_check.py score` (defaults: the rating file
and `work/T6/C1-T6-key.json`). The scorer's numbers equal the owner's
independent count.

| 12 units, single-rated 1–5 | mean | scores 5 / 4 / 3 / 2 / 1 |
|---|---|---|
| `pipeline-v3` (translation-v2.1 on sentence units) | **4.42** | 8 / 1 / 3 / 0 / 0 |
| Gemini `gemini-4-carbon`, plain prompt, one unit per request | 4.33 | 7 / 2 / 3 / 0 / 0 |
| difference, pipeline − Gemini | **+0.08** | paired bootstrap 95% interval **−0.33 … +0.50** (10,000 resamples of the units) |

- **Rule** (written before the run): absolute translation quality on T6 (first
  called "C1 on T6"; not the specification's C1) is met if the pipeline's mean
  >= 4.0. **Met** (4.42). The specification's C1 (at least 1 point above Google
  Translate) was rated on 2026-11-12 and is **not met** (Translation rating).
- **Against the baseline:** per unit, the pipeline scored higher on 2, Gemini
  on 2, 8 tied (2 of the ties are the shared items with identical text). The
  interval contains 0: the pipeline's translation is **not shown to be better
  than a plain Gemini translation** of the same corrected English. No other
  conclusion is drawn (protocol).
- **Caveats:** one rater (the owner, not a native English speaker); 12 units, not the specification's 20 (owner's amendment
  before the run); the rater reads the English first; the baseline translates
  the pipeline's corrected English, so it measures translation alone, not
  Gemini as a whole system. The lowest score of either system is 3.
- **Cost:** baseline 0.0240 USD (Gemini, prepared before the rating); scoring
  0 USD.

## Gate 2 -> 3 (2026-11-11)

The stage 2 gate, criterion by criterion (CLAUDE.md, Stage gates):

| Gate criterion | Result | |
|---|---|---|
| term accuracy >= 85% | T6 (hold-out, run once): 100% against J and M, lower bound 91.67% (Wilson 83.0%), verdict **pass (not demonstrated)**, no overfitting signal; T2 repeats 94.39–96.26% | met (not demonstrated at 85% with 72 occurrences) |
| blind translation score >= 4/5 (absolute translation quality on T6) | T6 check: pipeline mean 4.42 (12 units, single-rated, blind), Gemini 4.33, difference +0.08 [−0.33, +0.50] | met |
| hold-out T6 run once | 2026-11-11, `pipeline-v3` (d180a73), one run | met |
| independent LLM review | review #1, 2026-11-11 ([findings and responses](review-2026-11-11.md)) | met |

**Owner's decision (2026-11-11): gate 2 -> 3 passed.** Stage 3 finishes v1.0
as the specification lists it (benchmark with the baselines on one set, the
README in the series structure, re-run from cache within a minute, external
review #2, tag `v1.0`); the web UI stays v1.1. `pipeline-v3` stays frozen.

The gate was decided on term accuracy, the criterion the stage was about.
The specification's C1 (translation at least 1 point above Google Translate)
was rated only afterwards, on 2026-11-12, and is not met (Spec criteria).

What the gate does and does not show: the pipeline's strength is **term
recognition** (key terms from an automatic glossary) and the end-to-end tool;
its translation is **on par with a plain Gemini translation**, not better.
The references had no native listener, and T6's topic is close to T1 and T2,
so transfer to another domain is not shown (T3).

## Freeze pipeline-v3

Frozen on 2026-11-11 with the annotated git tag `pipeline-v3` on commit
**`d180a73ddc3c95f2b7cb79e8621e541aa36f99b5`**, after independent LLM review #1. It
supersedes `pipeline-v2` for the T6 run; T6 has never been run on either.
What changed against `pipeline-v2`:

- `glossary-auto-v2` and `translation-v2.1`: the same rules as
  `glossary-auto-v1` and `translation-v2`, with every example that came from
  the test set replaced by an example of the same kind from outside it
  (checked by code not to occur in the T1, T2 and T6 glossaries and
  references, `tests/test_prompt_examples.py`; no prompt or setting was tuned
  on T6). `correction-v2` is unchanged (its examples
  are discourse markers and an invented accent pair).
- The cache key of every step includes the prompt file and a hash of the
  validator and post-processing code (`pipeline.code_hash`), so a fix
  invalidates cached results.
- AssemblyAI key terms are truncated to the service's 1,000 words (every word
  of a phrase counts), the user's terms first, then the auto-glossary's by
  frequency in the first pass; nothing raises.
- The metric is term accuracy v2 (Metrics above); the metric code is frozen
  with the pipeline.

Frozen paths (a change to any of them after the tag needs a new tag and a
new hold-out): `talk_autopsy/ingest.py`, `talk_autopsy/stt/`,
`talk_autopsy/align.py`, `talk_autopsy/glossary.py`,
`talk_autopsy/glossary_auto.py`, `talk_autopsy/correct.py`,
`talk_autopsy/blocks.py`, `talk_autopsy/units.py`, `talk_autopsy/translate.py`,
`talk_autopsy/segments.py`, `talk_autopsy/pipeline.py`, `talk_autopsy/config.py`,
`talk_autopsy/core/`, `talk_autopsy/metrics.py`, `talk_autopsy/evaluation.py`,
`talk_autopsy/ref_bounds.py`, `talk_autopsy/prompts/glossary-auto-v2.md`,
`talk_autopsy/prompts/correction-v2.md`, `talk_autopsy/prompts/translation-v2.1.md`,
`uv.lock`. Check: `git diff --stat pipeline-v3 -- <these paths>` is empty.

Settings: models `claude-haiku-5-5` (auto-glossary) and `claude-sonnet-5-5`
(correction, translation) as the code's defaults, `.env` settings that select
a model or a version empty; AssemblyAI `universal-3-5-pro`, two passes;
translation `translation-v2.1` (`--translation v3` stays an option, not
frozen). Not frozen: subtitle layout, the CLI's printing, the cost estimate,
the evaluation tooling (`judge.py`, `translation_check.py`).

### Repeats on T2 and T1 (2026-11-11)

- **Why:** independent LLM review #1, finding 12: no number had a repeat. These three
  T2 runs are also the comparison point of the T6 overfitting signal.
- **Run:** `talk-autopsy run <fragment>.wav --lang ru --name <run>` from a clean
  `git worktree` of `pipeline-v3` (d180a73), a fresh work folder per run (no
  cache), no context, no hand glossary, keys from the main checkout's `.env`
  (`uv run --env-file`). Scored from the main checkout:
  `eval <fragment> --hyp <run>/fixed.json --raw <run>/raw2.json --record
  --engine pipeline-v3 --stage fix`. Repeats are numbered in the order they
  ran (the work names differ: the first start of `T2-v3-r1` failed before
  running, so it ran last). No fallback in any run.

| | T2 repeat 1 | T2 repeat 2 | T2 repeat 3 | T2 range (mean) | T1 |
|---|---|---|---|---|---|
| term accuracy v2, micro | 95.33% (102/107) | 94.39% (101/107) | 96.26% (103/107) | 94.39–96.26% (95.33%) | 99.20% (124/125) |
| macro | 87.18% | 84.62% | 89.74% | 84.62–89.74% | 98.65% |
| without `Claude Code` | 93.67% | 92.41% | 94.94% | 92.41–94.94% | 98.80% |
| precision | 98.08% | 98.06% | 99.04% | 98.06–99.04% | 100.00% |
| WER | 4.24% | 4.43% | 4.48% | 4.24–4.48% | 0.50% |
| false-edit rate | no edits | no edits | no edits | no edits | no edits |
| auto-glossary terms (= key terms) | 29 | 26 | 29 | 26–29 | 26 |
| Claude, whole run | 0.1400 USD | 0.1381 USD | 0.1369 USD | | 0.0958 USD |
| AssemblyAI, two passes (list price) | 0.0783 USD | 0.0783 USD | 0.0783 USD | | 0.0741 USD |

- **Correction:** 0 segments changed in all four runs (0 edits listed by the
  model, 0 word edits by the metric), at 0.064–0.065 USD per T2 run. On this
  path the key terms do the work; correction is kept as insurance for the
  fallback engines, but that is not shown for the shipped version: the
  evidence (B1: faster-whisper T2 input 61.7% -> 93.5%, scored with v2) is
  correction-v1 with a reference-reviewed glossary; correction-v2 with an
  automatic glossary was never run on fallback input, and no fallback path of
  `pipeline-v3` was run end to end and scored.
- **Macro below the target in one repeat:** repeat 2 has macro 84.62%, below
  85%. The 85% target is defined on the micro-average (Spec criteria, Term
  accuracy); the macro-average is reported, not gated.
- **Spread:** three runs differ by 1.9 points micro and 5.1 points macro on
  T2, from the auto-glossary (26–29 terms per run) and AssemblyAI's second
  pass; the earlier single-run number (B1 v2, 96.26%) is the top of the range.
- **T6 overfitting signal:** T2 mean 95.33%, so the signal fires if T6
  (reference J, micro) is below 85.33%.
- **Spend:** Claude 0.5108 USD, AssemblyAI 0.3090 USD (list price, free
  credit), Gemini 0.

## Freeze pipeline-v2

Frozen on 2026-11-10 with the annotated git tag `pipeline-v2`. The freeze
covers everything that determines the text the T6 run is scored on. A change
to any frozen part after the tag needs a new freeze tag and a new T6-like
hold-out: T6 can judge only this version.

| Part | Frozen as | Where |
|---|---|---|
| Audio | 16 kHz mono WAV for alignment; FLAC copy uploaded | `talk_autopsy/ingest.py` (`extract_audio`, `compress_audio`) |
| Speech recognition, first pass | AssemblyAI `universal-3-5-pro` (`speech_models`, the model used recorded from `speech_model_used`), `language_code=en`, speaker labels off, no key terms; segments = the service's sentences | `talk_autopsy/stt/assemblyai.py`; SDK `assemblyai` 1.8.2 (`uv.lock`) |
| Auto-glossary | prompt `glossary-auto-v1` (git blob `0c7088095fa4`), `claude-haiku-5-5`, from the first transcript and `--context`, at most 60 terms, evidence rule; merged with a user glossary (none on T6) | `talk_autopsy/glossary_auto.py`, `talk_autopsy/prompts/glossary-auto-v1.md` |
| Speech recognition, second pass | AssemblyAI as above with the glossary as `keyterms_prompt` | `talk_autopsy/stt/assemblyai.py`, `talk_autopsy/pipeline.py` (`stt2`) |
| Alignment | pass-through for AssemblyAI (measured times); wav2vec2 alignment only for model-estimated times (fallback engines) | `talk_autopsy/align.py`, `talk_autopsy/pipeline.py` (`align`) |
| Correction | prompt `correction-v2` (git blob `14ebec334842`), `claude-sonnet-5-5`, blocks of 300 s with 3 read-only context segments, the validator in `correct.py` (70% of words kept in order, no runs from a neighbouring segment, glossary terms kept or merged into another term), one retry | `talk_autopsy/correct.py`, `talk_autopsy/blocks.py`, `talk_autopsy/prompts/correction-v2.md` |
| Sentence units | `sentence-units-v1`: merge to terminal punctuation or a pause over 1.5 s, split above 25 s | `talk_autopsy/units.py` |
| Translation | prompt `translation-v2` (git blob `903af80b234a`), `claude-sonnet-5-5`, on sentence units, blocks of 300 s with 3 read-only context units, the validator in `translate.py`, one retry; target language `ru` | `talk_autopsy/translate.py`, `talk_autopsy/prompts/translation-v2.md` |
| Claude calls | structured output, `max_tokens` 16000, refusal fallback on in `run` (off in `fix` / `translate` / `eval`); SDK `anthropic` 2.6.0 | `talk_autopsy/core/structured_call.py`, `talk_autopsy/config.py` |
| Model IDs | `claude-sonnet-5-5` (correction, translation), `claude-haiku-5-5` (auto-glossary): the code defaults; `CORRECTION_MODEL`, `TRANSLATION_MODEL`, `HELPER_MODEL` must stay empty in `.env` | `talk_autopsy/config.py` |
| Fallback engines | chunked Gemini (`gemini-3.8-flash`, then `gemini-3.6-flash`) + alignment, then faster-whisper large-v3; used only if AssemblyAI fails, and the run then records the fallback | `talk_autopsy/stt/`, `talk_autopsy/pipeline.py` |

Not frozen: subtitle layout (`subtitles.py`: line breaking, cue timing,
display extension, the `.ass` style), the CLI's printing, the cost estimate,
the metrics code (a change to a metric rescores every row and is recorded as
such). The T6 run is `talk-autopsy run` from the tagged commit with the
auto-glossary and no hand glossary (T6 protocol).

## Operational runs

Whole videos through the production `run` (not scored: no reference, no
benchmark row). Default path since 2026-11-09: AssemblyAI first pass on
compressed audio (`audio.flac`), auto-glossary (Haiku), AssemblyAI with the
glossary as key terms, correction-v2 and translation-v2 (Sonnet, refusal
fallback on), `.srt` + `.ass` in Russian and English. No `--context` and no
user glossary in either run. Laptop as in the stage 1 runs; network times
include upload.

| | T2.source (talk, non-native speaker) | real-01 (interview, native speakers) |
|---|---|---|
| duration | 27:52 (1673 s) | 38:30 (2310 s) |
| command | `run data/test-set/T2.source.mp4 --lang ru` | `run <local file> --name real-01 --lang ru` |
| stt (extract audio, FLAC, AssemblyAI) | 34.5 s | 59.1 s |
| glossary (Haiku) | 17.4 s, 35 terms, 4 with corrected mis-recognitions | 19.3 s, 29 terms, 2 with corrected mis-recognitions |
| stt2 (AssemblyAI + key terms) | 35.6 s | 44.7 s |
| align | pass-through (measured times) | pass-through |
| fix (correction-v2, 6 / 8 blocks) | 79.2 s | 81.7 s |
| translate (translation-v2) | 105.4 s | 144.0 s |
| srt + ass | 0.8 s | 1.7 s |
| **total wall time** | **273 s (4.6 min): 9.8 min per hour of video** | **351 s (5.8 min): 9.1 min per hour** |
| limit of the plan | 30 min per hour | 30 min per hour |
| AssemblyAI (list price, two passes) | 0.218 USD = 0.47 USD per hour | 0.302 USD = 0.47 USD per hour |
| Claude estimate / spent | 0.532 / 0.380 USD = 0.82 USD per hour | 0.726 / 0.431 USD = 0.67 USD per hour |
| failed blocks | correction block 4/6 (see below); translation 0 | 0 / 0 |
| segments changed by correction | 3 (first run), 8 (rerun) | 1 |
| translation warnings (kept term missing) | 3 | 3 |
| Russian cues: above 17 / above 25 cps | 76.8% / 22.5% (422 cues) | 50.9% / 13.7% (591 cues) |
| English cues: above 17 / above 25 cps | 84.2% / 22.3% (430 cues) | 66.0% / 15.6% (582 cues) |
| fallbacks used | none (AssemblyAI answered both passes; no refusal) | none |

- **Auto-glossary on real videos.** T2.source: `Claude Code` <- "QuadCode",
  "Quad Code", "Cloud Code", "Cod Code"; `CLAUDE.md` <- "Claude MD", "Claude
  MT", "CloudMD", "Quad MD" and five more forms; `/install-github-app`;
  `.mcp.json` <- "MCP JSON". real-01: a course code heard as an ordinary
  word (and a harmless `GPU` <- "GPUs").
- **A validator bug found here and fixed.** On T2.source correction block 4/6
  failed validation twice ("glossary terms removed: Claude, MCP, JSON"), so
  its segments (211–271) kept the key-term transcript. Cause: the
  auto-glossary lists `.mcp.json` and `CLAUDE.md` as well as `MCP`, `JSON` and
  `Claude`; the right edits "MCP JSON" -> `.mcp.json` and "Claude MD" ->
  `CLAUDE.md` remove the shorter terms, which the old check took for lost
  terms. A removal is now excused when a listed edit turned those words into
  another glossary term (`correct.merged_into_term`, with a test). The
  T2.source correction, translation and subtitles were then run again (stamp
  of `fixed.json` deleted by hand; stt and glossary reused): 0 failed blocks,
  8 segments changed (`.mcp.json` twice, `CLAUDE.md` three times, `.claude`,
  `Claude Code`, `Claudes`), 0.347 USD (correction 0.163, translation 0.183).
  The first-run files are kept as `work/T2.source/fixed.first.json` and
  `ru.first.json`; the table above has the rerun's subtitle numbers and the
  first run's times and costs.
- **A CLI bug found here and fixed.** `run --name real-01` wrote to
  `work/srt/` with files `srt.*.srt`: the loop that prints the plan reused
  the variable `name`. Fixed and tested; the folder was renamed to
  `work/real-01/` and the subtitles rewritten with `--steps srt` (no API call).
- **Reading speed.** Nothing is cut (owner decision), so the shares are high
  on the fast talk by a non-native speaker and lower on the interview. Many fast cues are
  short sentences (AssemblyAI segments by sentence) that cannot linger
  because the next cue starts at once; cues never cross a segment boundary.
- **Since 2026-11-10** the Russian files of both videos are the sentence-unit
  translation with the new display rule (D1 (setup)); the files this table
  describes are kept as `ru.v2seg.json` and `<name>.<lang>.v2seg.srt` / `.ass`.
- **Outputs:** `work/T2.source/T2.source.ru.srt` / `.ass`,
  `T2.source.en.srt` / `.ass`; `work/real-01/real-01.ru.srt` / `.ass`,
  `real-01.en.srt` / `.ass`. The outputs of the earlier Gemini run are in
  `work/T2.source.prompt05/`. Of the money: the first start of the T2.source
  run was stopped by hand after its upload had begun (the old outputs had
  not been backed up yet); its AssemblyAI pass (up to 0.098 USD) is counted
  as spent in the day's total.

### YouTube input and output formats (2026-11-10; no pipeline run)

- **Smoke test not run:** `work/youtube-test-url.txt` did not exist, so no
  video went through the pipeline (0 USD Claude, 0 USD AssemblyAI). The
  command for the owner: `uv run talk-autopsy run "<url>" --domain web3
  --output all`.
- **Download path checked without the pipeline** (no API cost): `doctor`
  found ffmpeg, Node.js 24.15.0 (Deno is not installed; yt-dlp 2026.10.26
  supports Deno, Node.js 22+, Bun and QuickJS), `yt-dlp-ejs` 0.8.0 and the
  keys. `run <url> --dry-run` read a public video's metadata (title, channel,
  duration, captions de/en, 3 chapters); `youtube.download` fetched a 19 s
  public video as H.264 + AAC MP4 with the author's English captions as
  `.srt`. A removed video gave the error, the `doctor` hint and the
  local-file reminder.
- **Output formats on real files** (the T2.source pipeline outputs, 27:52,
  1080p, no API call): `srt`, `ass`, `bilingual`, `mp4-soft` (H.264 + AAC
  copied, two `mov_text` tracks: rus default, eng), `mp4-burned` (Russian
  drawn in, checked on a frame) in 457 s together. Burn-in alone: 10.7 s for
  a 60 s clip (0.18x real time); the estimate printed before burn-in uses
  0.27x from the full run.
- **web3 glossary:** 140 terms (the draft's grouped rows split into one term
  each, so each is a key term; all within AssemblyAI's 6-word and 1000-term
  limits). With translation-v2 every agreed translation is enforced in every
  segment where the term occurs, so everyday words with an agreed
  translation (`finding`, `block`, `coverage`) can make a block fail
  validation; translation-v3 does not enforce them.

### stress-01: the frozen pipeline on an interview with non-native speakers (2026-11-10, not scored)

An interview, 29:50 (a local file): two non-native speakers, one of them fast.
`talk-autopsy run <local file> --name stress-01 --lang ru`
from the tagged code (`pipeline-v2`), no `--context`, no user glossary.
Outputs `work/stress-01/stress-01.ru.srt` / `.ass`, `stress-01.en.srt` / `.ass`.

| | stress-01 |
|---|---|
| duration | 29:50 (1790 s) |
| stt (extract audio, FLAC, AssemblyAI) | 42.8 s, 335 segments |
| glossary (Haiku) | 22.2 s, **32 terms**, 5 with corrected mis-recognitions (`Claude Code` and four product-specific terms) |
| stt2 (AssemblyAI + 32 key terms) | 36.3 s, 346 segments |
| align | pass-through |
| fix (correction-v2, 6 blocks) | 71.8 s, 1 segment changed, 0 failed blocks |
| translate (translation-v2, 346 units from 346 segments, 6 blocks) | 120.1 s, 0 failed blocks, 0 warnings |
| srt + ass | 3.1 s |
| **total wall time** | **296.5 s (4.9 min): 9.9 min per hour of video** |
| AssemblyAI (list price, two passes) | 0.234 USD = **0.47 USD per hour** |
| Claude estimate / spent | 0.604 / 0.361 USD = **0.73 USD per hour** |
| Russian cues: above 17 / 25 cps | **51.3% / 14.8%** (446 cues; max 58.0 cps; 20 shorter than 1 s) |
| English cues: above 17 / 25 cps | 63.3% / 15.3% (450 cues; max 48.1 cps; 24 shorter than 1 s) |
| fallbacks used | none (AssemblyAI answered both passes; no refusal fallback served) |

- **Where the fast cues are.** No speaker labels (a v1.1 idea), so the turns
  cannot be told apart; the English speech rate of each unit stands in.
  Russian cues of units spoken at more than 3.5 English words per second:
  284 cues, 66.5% above 17 cps and 20.8% above 25 cps; at 2.5–3.5 words per
  second: 124 cues, 29.0% / 4.0%; below 2.5: 38 cues, 10.5% / 5.3%. Questions
  (units ending in "?", likely the interviewer's turns; not checked by ear) are not faster than the
  rest: 47 cues, 46.8% / 14.9%.
- **Visibly wrong.** The two fastest Russian cues (58.0 and 43.5 cps) last
  0.22 s and 0.16 s: the first clause of a unit cut into several cues, mapped
  by word share onto a single short English word. Word timing gives such a
  cue no more time than that word (Backlog, cue timing inside a unit). The
  rest of the top are one-sentence units that AssemblyAI times at 6–10 words
  per second (two- or three-word sentences).

Speech recognition on T1 (native speaker, 9:27) and T2 (non-native speaker, 10:00),
scored 2026-11-09 against the resolved references with the reviewed glossaries
(`results/benchmark.csv`, rows without a `superseded` note). Ten of ten
test-set runs used.

| Engine variant | T1 WER | T1 terms | T2 WER | T2 terms | Speed (10 min of audio) | Cost per hour of audio | Audio goes to |
|---|---|---|---|---|---|---|---|
| faster-whisper large-v3, 4 threads (A1) | 11.20% | 55.1% | 6.19% | 61.7% | about 22 min (0.44–0.46x real time) | 0 | local |
| faster-whisper + glossary prompt, 8 threads (A2) | 4.32% | 96.6% | 6.43% | 51.4% | 17 min (0.57x) on T1; 53 min (0.19x) on T2 | 0 | local |
| AssemblyAI Universal-3.5 Pro (A3 (setup)) | 1.41% | 95.8% | 6.97% | 80.4% | 35–39 s (about 15x) | $0.21 list | AssemblyAI |
| AssemblyAI + glossary keyterms (A3) | 0.33% | 100.0% | 4.43% | 99.1% | 25–27 s (about 23x) | $0.26 list | AssemblyAI |
| Gemini `gemini-3.8-flash`, one call (A4 (setup)) | 1.41% | 99.2% | 2.34% | 93.5% | 7.4–10 min incl. 503 retries | 0 on the free tier ($0.18–0.23 at paid prices) | Google |
| Gemini + forced alignment (A5 (setup)) | same text as Gemini | | same text as Gemini | | + about 1 min alignment (about 10x) | as Gemini | Google, then local |

1. **Gate (WER ≤ 15% on T2).** Every variant is under it on T2: the highest is
   AssemblyAI without keyterms at 6.97%, Gemini is lowest at 2.34%. The gate
   does not separate the engines; their differences show in term accuracy and
   in the biases below.
2. **Term accuracy against the stage 2 target (≥ 85%).** Without any glossary,
   Gemini already reaches 93.5% on T2 and 99.2% on T1; faster-whisper
   reaches 61.7% / 55.1% and AssemblyAI 80.4% / 95.8%. With the glossary given
   to the engine, AssemblyAI reaches 99.1% / 100% and faster-whisper 51.4% /
   96.6%. The project plan's stop rule is not in the repository, so it is not
   applied here; the facts it needs are these: on Gemini input the stage 2
   target is already met before any correction on both fragments, and on
   faster-whisper input it is not met on T2 by any measured variant.
3. **Biases of these numbers.** (a) AssemblyAI and Gemini helped build both
   references, so their WER is biased low (methodology section; their share
   of the T2 span decisions was Gemini 71, faster-whisper 52, AssemblyAI 30,
   typed 29 of 114). (b) T1's reference was decided by a Claude model without
   listening. (c) The glossaries were reviewed against the references, so
   every term is known to occur; the keyterms and prompt variants had that
   knowledge, which an unseen video would not give them. (d) Two fragments,
   one speaker each. (e) Gemini's wall times include waits for 503 retries
   and depend on the day's load; the free tier's cost is 0 but not
   guaranteed. (f) The alignment row's timing quality is measured against
   faster-whisper's word times, a proxy.

Owner direction recorded 2026-11-09 in decisions: Gemini as the main engine, Whisper as fallback, free tier only; formal decision at the gate.

## Summary

| ID | Date | Hypothesis | Run | Numbers | Conclusion | Decision |
|---|---|---|---|---|---|---|
| T6 key-term ablation | 2026-11-12 | none: what the key terms do on the hold-out | `eval T6` on existing transcripts (no API call): AssemblyAI without key terms, the run's first and second pass | without key terms 98.61% (71/72), with key terms 98.61%, after correction 100% | on T6 the key terms changed no occurrence; correction's one edit added the last; on T2 the key terms do the work | recorded; README "what does the work" gives T2 and T6 |
| C1 (baselines) | 2026-11-12 | the pipeline's blind mean is at least 1 point above Whisper + Google Translate (spec C1, Google Translate part) | owner rated `C1-T6-rating-2.md` (12 Google Translate items + 4 anchors) blind; pipeline and Gemini from the first T6 rating; `translation_check.py score-baselines` | pipeline 4.42, Google Translate 4.42, Gemini 4.33; pipeline − Google Translate 0.00 [−0.42, +0.42]; anchor drift 0.00 (4/4 identical) | **not met** (0.00 < 1.0): on random sentences the three systems rate equal; the difference in terms is an inference from the English side | accepted as a finding (owner, 2026-11-12): not confirmed; no translation changes before v1.0 (decisions) |
| LLM rater | 2026-11-12 | none: can Gemini serve as a second translation rater? | `gemini-rater-v1` on all 38 items, compared with the owner after the owner rated | Spearman 0.05; Gemini's own translations 4.93 vs pipeline 3.93, Google Translate 3.83; rater − owner +0.57 on Gemini items, −0.58 on the others | self-preference measured; no agreement with the owner | Gemini rater not usable; owner's rating stays the only one |
| T6 (hold-out) | 2026-11-11 | the frozen pipeline keeps term accuracy v2 >= 85% on an unseen fragment (T6 protocol: bounds, Wilson intervals, overfitting signal) | `run` once from a clean worktree of tag `pipeline-v3` (d180a73), auto-glossary only, no cache; `eval T6 --record` | J 100.00% (72/72), M 100.00% (71/71), lower 91.67% (Wilson 83.0–96.1%), upper 100.00%; WER 2.89–4.70%; correction 1 edit, 0 false; 52.5% of Russian cues above 17 cps; 0.140 USD Claude, 0.078 USD AssemblyAI | **pass (not demonstrated)**, the best verdict T6 allows; overfitting signal no; same-topic transfer only | recorded; translation check on T6 prepared, owner rates next |
| pipeline-v3 repeats | 2026-11-11 | none: variance of the frozen path, and the T2 point of the T6 overfitting rule | `run` from a clean worktree of tag `pipeline-v3`, auto-glossary only, no cache; T2 three times, T1 once | term accuracy v2 T2 94.39–96.26% (mean 95.33%), macro 84.62–89.74%, without `Claude Code` 92.41–94.94%; WER 4.24–4.48%; T1 99.20%; correction 0 edits in all four; 0.511 USD Claude, 0.309 USD AssemblyAI | the earlier single-run 96.26% is the top of the range; correction does nothing on this path | T2 mean 95.33% is the overfitting comparison point (Freeze pipeline-v3) |
| C1b | 2026-11-10 | translation-v3 (term classes keep / both / translate / ui) renders ordinary words in Russian without losing names or code, and the owner prefers it blind | `translate --prompt translation-v3` from the cached corrected transcripts of real-01 (428 units) and T2 (149 units); term classes by Haiku; 16 blind pairs | other Latin words per 100 Russian words real-01 0.81 -> 0.25, T2 2.10 -> 0.74; keep terms 43/44 -> 44/44 and 89/89 -> 89/89; 0 failed blocks, 0 marker warnings; above 17 cps real-01 49.2% -> 50.0%, T2 58.8% -> 65.1%; 0.346 USD | the owner's example words now follow the policy; T2 reads slower | **not adopted** (owner's blind rating 2026-11-11: v3 won 5 pairs, v2 6, ties 5; means v2 4.44, v3 4.25): v2 stays the default, v3 an option |
| D1 (setup) | 2026-11-10 | none: sentence units before translation and display extended into pauses (subtitle-v2) | translation-v2 on units; T2 fragment, C1 input, T2.source, real-01 | T2 fragment Russian above 17 / 25 cps 74.7% / 25.3% -> 58.8% / 15.8%; on AssemblyAI input units = segments (149/149, 373/373, 428/426); Gemini input 109 -> 85 units; 0.547 USD | the C1 fragments become whole sentences; the extension does most of the reading-speed work; word timing raises speed variance in multi-cue segments | units frozen with the pipeline; display rule stays open |
| C1 (blind) | 2026-11-10 | translation-v2 scores higher than v1 and >= 4.0 in a blind pairwise comparison | owner rated items 1–15 of `C1-blind.md` (T2, Gemini+align input, correction-v1 English) | v1 4.40, v2 4.67 over 15 pairs; v2 better 8, v1 better 3, ties 4 | v2 preferred; not fully blind | translation-v2 adopted. *Relabelled 2026-11-11 (review #1, finding 6): v2 preferred to v1; absolute target not demonstrated (3.85 < 4.0); this was recorded as "C1 met" through the wrong instrument (pairwise scores, not the specified single rating against baselines)* |
| B2 (v2) | 2026-11-10 | correction-v2 edits distort meaning in < 5% of cases | owner checked `B2-review-v2.md` | T2 0 edits; T1 2 edits, 0 distort meaning | met on a tiny sample | correction-v2 frozen unchanged |
| B1 (v2) | 2026-11-09 | correction-v2 with the auto-glossary keeps the realistic pipeline at >= 85% term accuracy without false edits or words moved between segments | `correction-v2`, Sonnet, on AssemblyAI + auto key terms, auto-glossary; T2, T1 | term accuracy T2 96.26% -> 96.26%, T1 98.33% -> 98.33%; WER T2 4.73% -> 4.73%, T1 0.58% -> 0.50%; false edits 0; edits 0 / 2; 0.108 USD | the realistic pipeline is at 96.3% / 98.3%; correction adds nothing measurable on this input | correction-v2 is the default; owner checks `B2-review-v2.md` |
| B5 | 2026-11-09 | a glossary drafted by Haiku from the first transcript covers >= 70% of the reference glossary | `glossary-auto-v1`, `claude-haiku-5-5`, raw AssemblyAI T2/T1, no context; two runs; then AssemblyAI with the auto terms | coverage T2 74.4% / 79.5%, T1 56.8% / 62.2%; STT terms with auto terms T2 96.3%, T1 98.3% (reference glossary: 99.1% / 100%); 0.0096 USD Claude, 0.084 USD AssemblyAI | met on T2, not on T1; the second pass keeps most of the keyterm gain | auto-glossary is the pipeline's glossary step |
| C1 | 2026-11-09 | translation-v1 subtitles score >= 4.0 with the owner | owner rated 20 random T2 segments (one system, not yet blind) | mean 3.85; 5 x8, 4 x4, 3 x5, 2 x3 | under the target; all three 2s lose meaning through the compression rule | translation-v2 without meaning compression (decisions); blind comparison next |
| B2 | 2026-11-09 | correction-v1 edits distort meaning in < 5% of cases | owner checked the 3 edits on T2 Gemini input | 0 of 3 distort meaning | met on a tiny sample | recorded; re-checked on v2 (`B2-review-v2.md`) |
| T1 re-score | 2026-11-09 | none: reference fix `@Claude` -> `@claude` | `resolve T1 --force`, T1 rows re-recorded | WER unchanged; term accuracy now counts `@claude` (2 occurrences) | only AssemblyAI + keyterms writes `@claude` | old T1 rows marked `superseded: @claude fix` |
| B4 | 2026-11-09 | Opus corrects terms better than Sonnet on the same input | correction-v1, `claude-opus-5-5`, T2 Gemini+align input | term accuracy 100% vs Sonnet 96.3%; WER 2.00% vs 2.14%; false edits 0 both; 0.129 vs 0.060 USD | Opus fixed the 4 terms Sonnet left (one fragment, one run each) | Sonnet stays the default; Opus noted as the option where terms are dense |
| B1 | 2026-11-09 | Claude correction lifts term accuracy to >= 85% without false edits | correction-v1, `claude-sonnet-5-5`, inputs: Gemini+align and raw faster-whisper, T2 and T1 | T2 Gemini 93.5% -> 96.3%, faster-whisper 61.7% -> 94.4%; T1 Gemini 99.2% (no edit), faster-whisper 55.1% -> 100%; false-edit rate 0 in all four; 0.23 USD | holds on all four; T1 evidence is secondary (Claude-decided reference) | correction-v1 kept; owner checks the T2 edits (B2) |
| A5 (setup) | 2026-11-09 | none: forced alignment of the Gemini transcripts, before Gemini can drive subtitles | wav2vec2 base 960h (torchaudio 2.11.1, CPU) + numpy Viterbi; existing Gemini transcripts of T2, T1 | 0 unplaced words; 8.6x / 10.3x real time; start times vs faster-whisper: median 130 / 230 ms, p95 410 / 660 ms | Gemini text gets measured word times; aligner starts words later than Whisper by a steady offset | `gemini+align` engine built on it; smoke test on a full talk below |
| A2 | 2026-11-09 | glossary as Whisper `initial_prompt` raises term accuracy by >= 20 points | faster-whisper large-v3 + `--glossary-prompt`, 8 CPU threads; T2, T1 | T2 term accuracy 61.7% -> 51.4%, WER 6.19% -> 6.43%; T1 55.1% -> 96.6%, 11.20% -> 4.32%; realtime factor 0.19 (T2) / 0.57 (T1) | holds on T1, fails on T2 (the accented fragment, the one that matters) | not adopted as a fix for accented speech; Whisper stays the fallback engine |
| A3 | 2026-11-09 | glossary as AssemblyAI keyterms raises term accuracy | Universal-3.5 Pro + keyterms (reviewed glossaries); T2, T1 | T2 term accuracy 80.4% -> 99.1%, WER 6.97% -> 4.43%; T1 95.8% -> 100%, 1.41% -> 0.33%; 0.084 USD | holds on these numbers; size inflated by a glossary built from the reference and by reference bias | recorded; re-test with a reference-free glossary on T3–T5 |
| A4 (setup) | 2026-11-09 | none: Gemini transcripts for the consensus reference | gemini-3.8-flash, Files API, structured JSON, no glossary; T2, T1 | 446 s / 602 s wall incl. 503 retries; 0 USD (free tier; 0.038 / 0.029 USD at paid prices); agreement with the backbone 95.3% / 91.9% | runs end to end; frequent 503 "high demand" | transcripts feed the T1 and T2 adjudication |
| A3 (setup) | 2026-11-09 | none: AssemblyAI transcripts for the consensus reference | Universal-3.5 Pro, no keyterms; T2, T1 | 39 s / 35 s wall; 0.035 / 0.033 USD list price (free credit); agreement with the backbone 91.8% / 92.4% | runs end to end, about 15x faster than real time | transcripts feed the T1 and T2 adjudication |
| A1 (setup) | 2026-11-09 | none: first run of the engine, for timings and drafts | faster-whisper large-v3, int8, CPU, no prompt; T1, T2 | about 22 min per 10-min fragment (realtime factor 0.44–0.46); no quality numbers | runs end to end; slower than real time on this laptop | drafts go to the owner for proofreading (superseded: the transcripts are the backbone of the consensus reference) |

### C1b: translation-v3, term classes (2026-11-10; rated 2026-11-11: not adopted)

- **Problem.** The owner watched the whole interview real-01 with
  translation-v2 subtitles: ordinary words stayed in English or became odd
  loanwords ("computer science", "tokens", "thinking tokens",
  "frontier-модель", "compute", "inference", "inference-компании",
  "alignment", "промптнуть", "эмерджентные свойства"). translation-v2 says
  "technical terms stay in English" without telling names and code from words
  that have a normal Russian translation; the same flaw was noted for UI names
  (Backlog).
- **Policy (owner, 2026-11-10; decisions):**

  | Class | Rule | Examples |
  |---|---|---|
  | `keep` | Latin script as is: names of products, companies, people, tools, libraries; code, commands, identifiers, file names; acronyms without a Russian form | Claude, OpenAI, Foundry, `msg.sender`, `forge test`, API, GPU |
  | `translate` | Russian only, no English | computer science -> информатика; tokens -> токены; thinking tokens -> токены рассуждений; frontier model -> передовая модель |
  | `both` | Russian with the English in brackets at its first occurrence in the video, Russian only afterwards | compute -> вычислительные мощности (compute); inference -> запуск моделей (inference); alignment -> согласование ИИ с интересами людей (alignment) |
  | `ui` | English as on screen, Russian gloss in brackets at the first occurrence, English only afterwards | Dictation (голосовой ввод); Command Palette (палитра команд) |

- **What was built.** A `policy` column in the glossary format (backwards
  compatible: `keep: yes` -> keep, `keep: no` with a translation ->
  translate); `glossaries/web3.md` mapped from the owner's draft (109 keep,
  21 translate, 10 both); `term_policy.py` (`term-policy-v1`): one Haiku call
  after the frozen auto-glossary classifies the terms without a policy, for
  the translation only (`glossary.md` and the speech recognition key terms are
  untouched: tested); `translation-v3`: v2 with the policy replacing
  "technical terms stay in English", plain renderings for rare scientific
  loanwords, `both` / `ui` terms marked inline and the first occurrence decided
  in code over the whole video; a validator that rejects a missing or
  transliterated keep term that looks like a name or code. `run --translation
  v2|v3`; the default stays v2.
- **Runs** (`translate --prompt translation-v3` from the cached corrected
  transcripts, the same sentence units as the v2 outputs the owner watched;
  `scripts/c1b_compare.py`): real-01 with its run glossary (29 terms, all
  classified by Haiku: 16 keep, 10 translate, 3 both: LLM, inference,
  compute), 171 s; T2 with `run.glossary.md` (31 terms: 21 keep, 7
  translate, 3 both: IDE, REPL, CLI), 54 s. Subtitles `work/real-01/v3/`,
  `work/T2/v3/` (`.srt` + `.ass`, layout as before).

| | real-01 v2 | real-01 v3 | T2 v2 | T2 v3 |
|---|---|---|---|---|
| units / failed blocks | 428 / 0 | 428 / 0 | 149 / 0 | 149 / 0 |
| Latin words of keep terms per 100 Russian words | 0.88 | 0.88 | 6.45 | 6.32 |
| other Latin words per 100 Russian words | **0.81** | **0.25** | **2.10** | **0.74** |
| keep terms preserved (term in the English -> in the Russian) | 43 / 44 (Twitter dropped) | 44 / 44 | 89 / 89 | 89 / 89 |
| marker warnings | - | 0 | - | 0 |
| cues above 17 / 25 cps | 49.2% / 14.8% | 50.0% / 14.6% | 58.8% / 15.8% | 65.1% / 21.9% |
| cost (translation + classification) | 0.230 USD | 0.263 + 0.001 USD | 0.071 USD | 0.081 + 0.001 USD |

  The keep-term split uses the v3 classified glossary for both versions.
  Reading speed is recomputed from both transcripts with the current layout.

  The owner's example words (units whose English has the word; first
  matching rendering per unit; real-01 unless noted):

  | Word | v2 | v3 |
  |---|---|---|
  | computer science (the speaker says "CS") | computer science (Latin) 3 | информатика 3 |
  | tokens | tokens (Latin) 5, токены 2 | токены 7 |
  | thinking tokens | thinking tokens (Latin) 1 | токены рассуждений 1 |
  | frontier | передовые 3, frontier-… 1 | передовые 4 |
  | compute | вычислительные мощности 12, compute (Latin) 3 | вычислительные мощности (compute) 1 (the first), вычислительные мощности 14 |
  | inference | inference (Latin) 4 | запуск моделей (inference) 1 (the first), запуск моделей 3 |
  | alignment | alignment (Latin) 1 | согласование ИИ с интересами людей (alignment) 1 |
  | prompt (real-01) | промпт / промптнуть 3 | запрос 2, вопрос 1 (prompt as a verb, rendered by a free paraphrase) |
  | prompt (T2) | промпт 9, other 3 | промпт 8, вопрос 1, other 3 |
  | emergent | эмерджентное / эмерджентными 2, other 1 | свойства, которые появляются при большом масштабе 2, other 1 |
  | Dictation (T2) | «Диктовка» 1, диктовка 1 | Dictation (диктовка) 1 (as a UI name), диктовка 1 (the key) |

- **Observations.** Every one of the owner's examples now follows the policy
  in real-01. The `both` brackets appear once per video, as designed (the
  first "compute" carries "(compute)", the next 14 do not). Haiku classified
  T2's `dictation` as translate, not ui; the model still wrote the macOS
  setting as `⟦ui:Dictation|диктовка⟧` (and Accessibility likewise), because
  the prompt lets it classify by context, but the gloss is «диктовка», not the
  owner's «голосовой ввод». On T2 v3 reads slower (cues above 17 cps 58.8% ->
  65.1%, above 25 cps 15.8% -> 21.9%): Russian renderings are longer than the
  English words v2 kept (`pull request` -> «запрос на слияние», IDE ->
  «интегрированная среда разработки (IDE)»); real-01 is unchanged. Haiku put
  `pull request` in translate, while Russian developers usually say
  «пул-реквест»; the blind rating will show whether that reads well.
- **Blind pairs for the owner:** `work/C1-v3-blind.md`, 16 pairs (10
  real-01, 6 T2) chosen by code (`scripts/c1b_blind_pairs.py`, seed
  20261110) among units where v2 and v3 differ, 12 with an example word
  (every example covered); A and B in random order per pair; the key in
  `work/C1-v3-key.json`. **Decision rule (written before the rating):** v3 is
  adopted if, in the owner's blind scores, v3 wins more pairs than v2 and v3
  has no pair with a score of 2 or less caused by lost meaning. Otherwise v2
  stays and the comments go to the Backlog.
- **Status (2026-11-11):** rated; **v3 not adopted, v2 stays the default**.
  translation-v3 stays available as `--translation v3`; the `pipeline-v2`
  freeze is unchanged and there is no new freeze tag.

#### Owner's blind rating (2026-11-11)

The owner filled in all 16 pairs of `work/C1-v3-blind.md`; the scores were
un-blinded with `work/C1-v3-key.json` by code (`scripts/c1b_rating.py`), not
by hand.

| Pair | Source | Example word | v2 | v3 | Winner |
|---|---|---|---|---|---|
| 1 | real-01 (unit 126) | - | 4 | 5 | v3 |
| 2 | real-01 (unit 218) | emergent | 4 | 4 | tie |
| 3 | real-01 (unit 224) | tokens | 5 | 5 | tie |
| 4 | real-01 (unit 261) | computer science | 5 | 4 | v2 |
| 5 | real-01 (unit 339) | thinking tokens | 5 | 3 | v2 |
| 6 | real-01 (unit 340) | frontier | 4 | 5 | v3 |
| 7 | real-01 (unit 342) | prompt | 4 | 5 | v3 |
| 8 | real-01 (unit 350) | alignment | 4 | 3 | v2 |
| 9 | real-01 (unit 408) | compute | 5 | 5 | tie |
| 10 | real-01 (unit 425) | inference | 4 | 5 | v3 |
| 11 | T2 (unit 47) | prompt | 4 | 4 | tie |
| 12 | T2 (unit 48) | Dictation | 3 | 5 | v3 |
| 13 | T2 (unit 49) | Dictation | 5 | 5 | tie |
| 14 | T2 (unit 85) | - | 5 | 4 | v2 |
| 15 | T2 (unit 108) | - | 5 | 3 | v2 |
| 16 | T2 (unit 109) | - | 5 | 3 | v2 |
| **Total** | 16 pairs | | **mean 4.44** | **mean 4.25** | **v3 5, v2 6, ties 5** |

- **Rule (written before the rating) and outcome.** v3 is adopted if v3
  wins more pairs than v2 and no v3 score of 2 or less is caused by lost
  meaning. v3 won 5 pairs, v2 won 6 (5 ties); the lowest v3 score is 3, so
  the second condition holds but the first does not: **v3 is not adopted, v2
  stays the default.**
- **The owner's comment** (the only one, pair 4, given in Russian): "a
  course in informatics" («курс по информатике») sounds unprofessional and
  primitive, as if it were addressed to schoolchildren or non-specialists.
- **Pattern** (observations from one rater on 16 pairs, not a measurement):
  - v3 won where it replaced jargon with a short, natural Russian word, or
    kept UI names as on screen with a short gloss: frontier model ->
    «передовая модель» (6); prompt -> «запрос» (7); inference -> «запуск
    моделей» (10); Accessibility and Dictation as on screen with a gloss (12).
  - v3 lost where the gloss was long (alignment with a five-word gloss, 8;
    REPL expanded into a full phrase, 16), where it replaced jargon the viewer
    reads fluently (thinking tokens and prompt as a verb, 5; computer science
    -> «информатика» read as a lower register, 4), and on idioms
    (15, 14).
  - Caveat: the rater sees the English sentence above both translations,
    which may favour keeping English words; while watching, the viewer only
    hears the English.
- **Next:** a translation-v4 candidate goes to the Backlog (after the T6 run,
  within the limit of 5 translation-prompt versions).

### Subtitles: up to 3 lines and `.ass` (operational, not scored)

Owner decision of 2026-11-09 (decisions): no meaning is cut for reading
speed. `subtitles.py` now lays cues out in up to 3 lines of up to 48
characters (fewest lines that fit, balanced; 1–7 s on screen; split at
sentence, then clause ends, never inside a glossary term; a cue that does not
fit becomes two cues inside the segment's time span) and writes a sibling
`.ass` for every `.srt` (1920x1080, Arial 42, white with a dark outline,
bottom centre, 60 px bottom margin, one style). Reading speed is reported per
file as the shares of cues above 17 and above 25 characters per second.
First use, the T2 fragment from the realistic pipeline (AssemblyAI + auto key
terms, correction-v2, translation-v2), `work/T2/T2.v2.ru.srt` / `.ass` and
`T2.v2.en.srt` / `.ass`:

| | Russian (translation-v2) | English (correction-v2) |
|---|---|---|
| cues | 166 | 172 |
| above 17 cps | 74.7% | 83.7% |
| above 25 cps | 25.3% | 26.7% |
| max cps | 66.1 | 82.6 |
| shorter than 1 s / longer than 7 s | 9 / 0 | 11 / 0 |

For comparison, translation-v1 (compressed) on the Gemini segmentation had
53% of its Russian cues above 17 cps. The fastest cues are short sentences
whose segment is short and whose next cue starts at once, so the cue cannot
linger; cues never cross a segment boundary. Writing both files takes about
2 s per fragment.

### D1 (setup): sentence units and longer display (subtitle-v2)

- **What and why:** two defects before the freeze. (1) Translating a
  recognition segment that holds half a sentence gives a fragment (C1 blind,
  items 3 and 5). (2) Cues left the screen 0.3 s after their last word, so
  most short cues were read faster than 17 characters per second. Not a
  scored hypothesis: no reference for reading speed or timing, numbers below
  are descriptive.
- **Change** (6eb76ea, 112580f): `units.py` regroups segments into sentence
  units before translation — consecutive segments merge until the unit ends
  with terminal punctuation or the pause after a segment exceeds 1.5 s; a
  unit over 25 s is split into the fewest parts, each break at the best
  boundary (sentence end, clause end, segment boundary, word) nearest the even
  split point. Each unit keeps its English words with timings and its source
  segment ids. Translation runs on units with the same prompt (translation-v2)
  and the same per-unit validator; `translate --per-segment` keeps the old
  path. Subtitles cut a translated unit's text into cues at sentence and
  clause ends and time each cue by the English words it covers (the same share
  of English words as of target characters, moved to an English sentence or
  clause end within 3 words). Every cue (both languages) then stays on screen
  into the following silence: up to +1.5 s, ending at least 0.1 s before the
  next cue, never longer than 7 s, no overlap. Correction is unchanged and
  segment-based; the English file is still cut from segments.
- **Finding: on AssemblyAI input units equal segments.** AssemblyAI segments
  are its sentences, and every one of them ends with terminal punctuation: T2
  fragment 149 segments -> 149 units, T2.source 373 -> 373, real-01 426 ->
  428 (two segments of about 28 s split, none merged). Merging happens on the
  fallback engines' input: the Gemini + alignment transcript of the T2
  fragment (the C1 input) has 24 of 109 segments without terminal punctuation,
  109 -> 85 units, 21 merged. So on the main path the change in the numbers
  below comes from the display extension and from word-based timing, not from
  merging.
- **The C1 fragments, re-translated on the C1 input** (Gemini + alignment,
  correction-v1 English, reference glossary; `work/T2/T2.ru.v2u.json`, 0
  failed blocks, 0.062 USD): items 3 and 5 are now whole sentences. Segments
  12 + 13 (a condition and its consequence, cut at the segment boundary) form
  one unit, and the Russian no longer starts in the middle of the sentence;
  segments 21 + 22 (the IDE list and the people who use Vim) form one unit. In
  that unit an idiom about editor loyalty came out as a calque, which the per-segment run had rendered by meaning
  (Backlog).
- **T2 fragment, realistic input** (AssemblyAI + auto key terms,
  correction-v2, auto-glossary; `translate` on units ->
  `work/T2/T2.v2u.ru.json`, 149 units, 0 failed blocks, 0.071 USD; subtitles
  `work/T2/T2.v2u.ru.srt` / `.ass`, `T2.v2u.en.srt` / `.ass`):

  | Russian cues | v2 per segment, 0.3 s linger (`T2.v2.ru.srt`, 2026-11-09) | v2 per segment, new display | **v2 units, new display** |
  |---|---|---|---|
  | cues | 166 | 166 | 165 |
  | above 17 cps | 74.7% | 58.4% | **58.8%** |
  | above 25 cps | 25.3% | 16.9% | **15.8%** |
  | max cps | 66.1 | 70.8 | 70.8 |
  | shorter than 1 s / longer than 7 s | 9 / 0 | 12 / 0 | 11 / 0 |

  English (correction-v2, cut from segments): above 17 cps 83.7% -> 68.0%,
  above 25 cps 26.7% -> 18.6%, 172 cues. The middle column is the same
  per-segment translation laid out with the new display rule (no API call),
  to separate the two changes: the extension does almost all of the work
  here. The fastest cues are one-word sentences that AssemblyAI times very
  short (Russian "Отлично." on 0.11 s) with the next cue right behind them;
  extension cannot help them (Backlog: short sentence cues). The maximum rose
  because the gap before the next cue is now 0.1 s instead of 0.04 s.
- **Word timing against character timing.** Inside a segment that needs
  several cues, the old path gave each cue time in proportion to its
  characters, so all cues of a segment had the same reading speed. Word
  timing follows the speaker: a cue appears when its English words are said
  and pauses fall between cues, but the reading speed varies from cue to cue,
  so more cues cross the thresholds. On real-01 the Russian cues of multi-cue
  segments go from 28.5% / 2.8% (above 17 / 25 cps, per segment, old layout)
  to 40.2% / 8.7% with units; single-cue segments are unchanged (58.7% /
  20.3% against 57.2% / 20.3%). Variants tried without an API call: mapping
  by English characters instead of English word count lowers the share above
  25 cps by 0 to 1.3 points; no snapping by 0 to 1.0 points. Not adopted
  before the freeze (subtitle layout is not frozen; Backlog).
- **Whole videos** (`run ... --steps translate,srt`: correction output
  reused, no speech recognition call; the per-segment outputs are kept as
  `ru.v2seg.json` and `<name>.<lang>.v2seg.srt` / `.ass`):

  | | T2.source before (per segment, 0.3 s linger) | T2.source after (units, new display) | real-01 before | real-01 after |
  |---|---|---|---|---|
  | units / segments | — | 373 / 373 | — | 428 / 426 |
  | Russian cues | 422 | 425 | 591 | 587 |
  | Russian above 17 / 25 cps | 76.8% / 22.5% | **62.4% / 17.6%** | 50.9% / 13.7% | **49.2% / 14.8%** |
  | English above 17 / 25 cps | 84.2% / 22.3% | 66.0% / 15.8% | 66.0% / 15.6% | 56.0% / 14.1% |
  | translation failed blocks / warnings | 0 / 3 | 0 / 3 | 0 / 3 | 0 / 2 |
  | translation time / cost | 108 s / 0.183 USD | 109 s / 0.184 USD | 144 s / 0.231 USD | 144 s / 0.230 USD |

  On real-01 the Russian share above 25 cps rose by 1.1 points: the interview
  has many multi-cue segments, where word timing costs more than the extension
  gains (per segment with the new display it would be 44.3% / 12.0%).
- **Cost of this entry:** 0.547 USD Claude (T2 fragment 0.071, C1 input
  0.062, T2.source 0.184, real-01 0.230); no speech recognition.
- **Decision:** sentence units go into the freeze (they decide the text that
  is translated); the display rule and the timing of cues are subtitle layout
  and stay open.
- **Reading speed in practice** (owner, recorded 2026-11-10): the owner watched
  the full 28-minute talk with the sentence-unit `.ass` subtitles. 59% of its
  cues were above 17 characters per second, yet the owner did not need to
  pause or slow down. 17 cps stays an informational metric, not a limit.

### B2 (v2): the owner's check of correction-v2's edits

- **Hypothesis:** fewer than 5% of correction-v2's edits distort the meaning of
  what was said (as B2 for v1).
- **Run:** the owner reviewed `work/T2/B2-review-v2.md`: the edits correction-v2
  made in B1 (v2) on the realistic input (AssemblyAI + auto key terms).
- **Numbers:** T2 — 0 edits, nothing to review. T1 — 2 edits (`@claud` ->
  `@Claude`, `Claude codes` -> `Claude Codes`); the owner confirmed that
  neither distorts meaning. `@Claude` against the convention `@claude` is a
  spelling difference, not a change of meaning.
- **Conclusion:** 0 of 2 distort meaning; the criterion is met on a very small
  sample (2 edits, on the fragment whose reference a Claude model decided).
- **Decision:** recorded; correction-v2 goes into the freeze unchanged.

### C1 (blind): translation-v1 against translation-v2

- **Hypothesis:** translation-v2 (no meaning compression) scores higher with
  the owner than translation-v1 and reaches the stage 2 target (mean >= 4.0)
  in a blind comparison.
- **What changed from v1** (`talk_autopsy/prompts/translation-v2.md`; owner's
  decision after C1): every content clause is translated, only disfluencies
  (fillers, repetitions, false starts) are dropped; idioms, set phrases and job
  titles by meaning, never as calques; technical terms that developers say in
  English stay in English (the owner rated these 5); kept glossary terms always
  stay; the rules that rewarded shortness ("short sentences", "compress where
  the target text would be much longer") are gone; the segment rules and the
  validator are unchanged. A glossary note "suggested ..." is a suggestion: the
  pipeline now passes an auto-glossary's drafted translations that way
  (`glossary_auto.merge`), since the validator rejects a block that does not
  use an agreed translation and a model's draft is not agreed.
- **Runs:** 2026-11-09, `translate --prompt translation-v2`,
  `claude-sonnet-5-5`, `ru`:
  1. on the same English and glossary as the rated v1 run
     (`work/T2/fixed.gemini+align.correction-v1.json`,
     `data/test-set/T2.glossary.md`) -> `work/T2/T2.ru.v2.json`: 109 segments,
     0 failed blocks, 0.064 USD (estimate 0.109). The C1 segment ids belong to
     this transcript; the AssemblyAI transcript of the realistic pipeline has
     149 different segments, so the blind comparison uses this input, and the
     two translations differ only in the prompt.
  2. on the realistic pipeline's English
     (`work/T2/fixed.assemblyai+autoterms.correction-v2.json`, auto-glossary
     with suggested translations `work/T2/run.glossary.md`) ->
     `work/T2/ru.assemblyai+autoterms.correction-v2.translation-v2.json`: 149
     segments, 0 failed blocks, 0.072 USD (estimate 0.110). This one feeds the
     subtitles, not the rating.
- **Facts before the rating:** on run 1, v2's Russian is 17% longer than v1's
  (9,637 against 8,229 characters; the English corrected text has 10,707);
  11 of 109 segments are identical. The three segments rated 2 now keep the
  clauses v1 dropped (segment 18 keeps the phrase about doing it all at once).
  A job title in segment 2 is still a word-for-word calque in both versions,
  despite the new rule (Backlog).
- **Blind file:** `work/T2/C1-blind.md`, the same 20 segments as
  `C1-rating.md`; for each the English and two Russian translations X and Y,
  v1 and v2 assigned at random per segment (seed and assignment only in
  `work/T2/C1-blind.key.json`, which the file tells the owner not to open
  before scoring). Caveat: the owner has already read and scored the v1
  translations of these 20 segments, so a remembered wording can unblind a
  segment; segments where X and Y are identical score the same by
  construction.
- **Rating (2026-11-10):** the owner filled the blind pairwise file for items
  1–15 and stopped there, judging both versions good. The remaining 5 items
  are not rated: the assistant did not rate them, since Claude would be rating
  Claude's own output.
- **Numbers** (unblinded with `C1-blind.key.json` after the scores):

  | | translation-v1 | translation-v2 |
  |---|---|---|
  | pairs rated | 15 | 15 |
  | mean | 4.40 | **4.67** |
  | better in the pair | 3 | 8 |
  | ties | 4 | 4 |

  v2's two weak items, 3 and 5 (segments 13 and 21, both rated 3), are
  sentence fragments cut at a segment boundary: the segment holds half a
  sentence and its translation reads as a fragment.
- **Caveats:** not fully blind — the owner had rated the v1 texts the day
  before, so a remembered wording can unblind an item. The same v1 texts
  scored 3.85 rated alone and 4.40 in pairs, so absolute scores drift by about
  one point between sessions: pairwise rating is for choosing between
  versions, single rating for "is it good enough". 15 items, one fragment,
  one rater.
- **Conclusion:** v2 wins more pairs than it loses (8 to 3): **v2 is
  preferred to v1**. *Relabelled 2026-11-11 (independent LLM review #1, finding 6):
  this entry first said "both versions are above the 4.0 target; C1 is met".
  Pairwise scores drift about one point above single ratings (see above), and
  the specification's C1 compares against Google Translate and VideoLingo,
  which were never run; the absolute target is not demonstrated (3.85 < 4.0,
  the only single rating). C1 is re-tested on T6 with a pre-registered
  single-rated check (T6 protocol).*
- **Decision:** translation-v2 adopted (decisions, 2026-11-10). The fragment
  defect is fixed below the prompt: translation runs on sentence units
  (D1 (setup)).

### B1 (v2): correction-v2 on the realistic input

- **Hypothesis:** with the input a real video gets — AssemblyAI with the
  auto-glossary as key terms (B5) and the same auto-glossary given to
  correction — correction-v2 keeps term accuracy at or above the stage 2
  target (>= 85%, scored against the **reference** glossary) with no false
  edits, and no longer moves words between segments.
- **What changed from v1** (`talk_autopsy/prompts/correction-v2.md`): edits
  stay inside a segment — no word moves to or from a neighbouring segment, a
  garbled segment is left alone rather than rebuilt from its neighbours (the
  full-talk run of v1 built an audience question out of the next segment's
  words, giving cues at 77–85 characters per second); the glossary is
  described as auto-drafted, with its "heard as" notes. Unchanged: fix only
  recognition errors, keep wording, leave anything uncertain. Validator
  (`correct.py`, for every version): a segment must keep >= 70% of its input
  words in order unless the listed edits cover every change; no output segment
  may hold a run of >= 3 words from a neighbouring input segment that its own
  input lacks (counted only when at least two of those words are new to the
  segment and not glossary words, so a one-word fix that happens to match a
  neighbour is not taken for a move). Tests for both rules.
- **Run:** 2026-11-09, `talk-autopsy fix --prompt correction-v2`,
  `claude-sonnet-5-5`, blocks of 5 minutes, glossary
  `work/T<n>/auto.glossary.md` (B5 run 1), inputs
  `work/T<n>/raw.assemblyai+autoterms.json`. Scored with
  `eval --raw <input>` against the reference transcripts and the reference
  glossaries.
- **Numbers:**

  | | T2 | T1 |
  |---|---|---|
  | term accuracy, input -> corrected | 96.26% -> 96.26% (103/107) | 98.33% -> 98.33% (118/120) |
  | WER, input -> corrected | 4.73% -> 4.73% | 0.58% -> 0.50% |
  | false-edit rate | 0.00% (0 of 1964) | 0.00% (0 of 1200) |
  | segments changed / edits | 0 / 0 | 2 / 2 |
  | failed blocks / validator rejections | 0 / 0 | 0 / 0 |
  | estimate / cost | 0.094 / 0.064 USD | 0.063 / 0.044 USD |

  T1's two edits: "@claud" -> "@Claude" (the reference writes `@claude`, so the
  term still counts as missed) and "Claude codes" -> "Claude Codes".
- **Conclusion:** the realistic estimate of the whole English pipeline (first
  pass, auto-glossary, second pass, correction) is **96.3% term accuracy on
  T2 and 98.3% on T1**, above the 85% target, against 96.3% / 98.3% before
  correction: on this input correction-v2 adds nothing measurable. The terms
  still missed on T2 (daltonized, at-mention, WebFetch, stand-up) are not in
  the auto-glossary in the reference's spelling, so neither the second pass
  nor correction had them. Whether v2 still prevents moved words is not
  tested here (no segment invited it); the validator tests cover the case.
  Biases: AssemblyAI helped build both references; T1's reference was
  decided by a Claude model; one run per fragment.
- **Decision:** correction-v2 is the default prompt (`correct.PROMPT_VERSION`);
  the owner checks the edits in `work/T2/B2-review-v2.md` (no T2 edit; the
  two T1 edits as secondary evidence). Correction stays in the pipeline: on
  faster-whisper or Gemini fallback input it did the most work (B1).

### B5: auto-glossary from the first transcript

- **Hypothesis:** a glossary drafted by the helper model from the raw
  transcript alone covers at least 70% of the reference glossary's terms with
  the right spelling (target from the project plan), so an unseen video gets
  useful key terms without a hand-made glossary.
- **Run:** 2026-11-09, `talk-autopsy glossary-auto` (new,
  `talk_autopsy/glossary_auto.py`), prompt `glossary-auto-v1`,
  `claude-haiku-5-5` (default adaptive thinking, effort medium), one call per
  fragment. Input: the existing raw AssemblyAI transcripts
  `work/T<n>/raw.assemblyai.json` (A3 (setup), no key terms; no new speech
  recognition run), no context (the fragments have none; a local file name is
  not context). The model returns 20–60 terms with canonical spelling, `keep`,
  a Russian translation when not kept, and `heard_as`: the transcript's forms it
  takes for mis-recognitions of the term. A term with no evidence in the
  transcript (neither its spelling nor a `heard_as` form) is dropped. Output
  `work/T<n>/auto.glossary.md`. The same prompt ran twice per fragment to see
  the variance (run 2 in `auto.glossary.run2.md`); run 1 is the one used below.
- **Coverage** = reference-glossary terms present in the auto-glossary with the
  right spelling, by the metrics' term rule (identifiers case-sensitive,
  regular plural of the last word accepted):

  | | T2 run 1 | T2 run 2 | T1 run 1 | T1 run 2 |
  |---|---|---|---|---|
  | auto terms | 31 | 35 | 24 | 26 |
  | coverage | **29/39 (74.4%)** | 31/39 (79.5%) | **21/37 (56.8%)** | 23/37 (62.2%) |
  | terms with corrected mis-recognitions | 4 | 3 | 4 | 3 |
  | noise (auto terms not in the reference) | 2 | 4 | 3 | 2 |
  | cost | 0.0026 USD | 0.0027 USD | 0.0022 USD | 0.0021 USD |
  | wall time | 20 s | 19 s | 17 s | 15 s |

  Run 1 details. T2 mis-recognitions corrected: `Claude Code` <- "QuadCode",
  "Cloud Code"; `/install-github-app` <- "/install GitHub app";
  `/terminal-setup` <- "terminal setup"; `codebase` <- "code base". Missing:
  daltonized, at-mention, allowed tools, onboarding, indexing, WebFetch,
  stand-up, LLM, commit, PR. Noise: "Daltonize" (the reference's word in
  another form), "web fetch" (the reference's `WebFetch` in its spoken form).
  T1 corrected: `Claude Code` <- "Quad Code", "QuadCode", "QuadCodes";
  `Claude` <- "claud", "@claud"; `GitHub Actions` <- "GitHub Action";
  `Claude Code SDK` <- "QuadCode SDK". Missing: Claude 4, research preview,
  generally available, beta, agentic, coding agent, SDK, @claude, repo,
  open-source, diffs, flaky tests, test coverage, on-call, triage, Slack.
  Noise: Claude Code SDK, agentic coding, backlog (real terms of the talk that
  the reference lists in parts or not at all). One term ("diff") was dropped
  by the evidence check because the transcript has only "diffs"; fixed after
  run 2 (a regular plural now counts as evidence); no coverage number changes,
  since the reference term is "diffs".
- **Second speech recognition pass:** AssemblyAI Universal-3.5 Pro with the
  run-1 auto-glossary as key terms (`--glossary-prompt`; 31 and 24 terms),
  `work/T<n>/raw.assemblyai+autoterms.json`, scored against the reference
  glossaries:

  | | no key terms (A3 (setup)) | auto terms (B5) | reference glossary terms (A3) |
  |---|---|---|---|
  | T2 term accuracy | 80.37% | **96.26%** (103/107) | 99.07% |
  | T2 WER | 6.97% | 4.73% | 4.43% |
  | T1 term accuracy | 94.17% | **98.33%** (118/120) | 100.00% |
  | T1 WER | 1.41% | 0.58% | 0.33% |
  | wall time / cost | | 25 s, 0.043 USD / 24 s, 0.041 USD | |

  T2 still misses daltonized, at-mention, WebFetch, stand-up (none in the
  auto-glossary); T1 misses `@claude` twice (written "@Claude").
- **Conclusion:** coverage meets the 70% target on T2 (both runs) and misses it
  on T1 (both runs); run-to-run spread is about 5 points. What the
  auto-glossary misses are mostly ordinary technical nouns (beta, repo, triage,
  flaky tests) that the recognizer already spells right; the names it was
  built for — the product name heard as "QuadCode" — it found on both
  fragments. With those terms as key terms the second pass keeps most of the
  keyterm gain: on T2 96.3% against 99.1% with the reference-checked
  glossary. Biases: AssemblyAI helped build both references (its WER is biased
  low); T1's reference was decided by a Claude model; two fragments.
- **Decision:** the auto-glossary is the glossary step of the pipeline (owner's
  direction); its suggested translations are suggestions, not
  agreed translations (translation-v2 records a deviation as a warning).
  Coverage on T1 is not chased with prompt changes against this reference;
  the hold-out T6 gives the honest number.

### C1: the owner's rating of translation-v1

- **Hypothesis:** Russian subtitles from `translation-v1` (Sonnet) reach the
  stage 2 target of a mean score >= 4.0 out of 5.
- **Run:** the owner rated `work/T2/C1-rating.md`: 20 segments of the corrected
  T2 fragment drawn with seed 20261109, English and Russian side by side, a
  score of 1–5 each. One system only, so this is not yet the blind comparison
  the gate asks for.
- **Numbers:** mean **3.85**; scores 5 x 8, 4 x 4, 3 x 5, 2 x 3.
- **Owner's comments (in the file):** all three segments rated 2 (4, 7, 10)
  lose or distort meaning — segment 4 drops the phrase about building whole
  features in one go. The cause in all three is the compression rule of translation-v1.
  English technical terms left untranslated (onboarding, indexing, codebase)
  were rated 5.
- **Conclusion:** under the target, and the low scores have one cause that the
  prompt asked for.
- **Decision:** no meaning compression from translation-v2 on; subtitles up to
  3 lines with an `.ass` file (decisions, 2026-11-09). The blind comparison
  of v1 and v2 on the same 20 segments follows (C1 blind).

### B2: the owner's check of correction-v1's edits

- **Hypothesis:** fewer than 5% of correction edits distort the meaning of what
  was said.
- **Run:** the owner reviewed `work/T2/B2-review.md`: the 3 edits correction-v1
  (Sonnet) made on the T2 Gemini + alignment input (B1): `/terminal:setup` ->
  `/terminal-setup`, `web fetch` -> `WebFetch`, `LM` -> `LLM`, each with the
  aligned reference text.
- **Numbers:** 0 of 3 distort meaning (the owner accepted the assistant's
  check).
- **Conclusion:** the criterion (< 5% distorting) is met on this sample. The
  sample is tiny: 3 edits on one fragment say little about the rate.
- **Decision:** recorded. correction-v2 gets the same check
  (`work/T2/B2-review-v2.md`).

### T1 re-scored after the `@claude` fix

- **What:** the reference conventions write the GitHub mention `@claude`; the
  assistant had adjudicated T1 spans 0052 and 0055 as `@Claude`, so the
  case-sensitive identifier matched nothing and `@claude` was excluded as absent.
  On 2026-11-09 (owner's instruction) both spans got `other: @claude` in
  `T1.adjudication.md` (no other span chose `@Claude`), `resolve T1 --force`
  rebuilt the reference (1205 words, only those two words changed), and every
  T1 row was recorded again; the previous T1 rows carry `superseded: @claude fix`.
- **Numbers** (term accuracy, 36 terms, 120 occurrences now; WER unchanged,
  the normalizer ignores case):

  | T1 run | before | after |
  |---|---|---|
  | faster-whisper large-v3 | 55.08% | 54.17% |
  | AssemblyAI | 95.76% | 94.17% |
  | Gemini | 99.15% | 97.50% |
  | AssemblyAI + keyterms | 100.00% | 100.00% (writes `@claude` 2/2) |
  | faster-whisper + glossary prompt | 96.61% | 95.00% |
  | Gemini + align + correction-v1 | 99.15% | 97.50% |
  | faster-whisper + correction-v1 | 100.00% | 98.33% |

  correction-v1 did not change "@Claude" to `@claude` on either input. The
  stage 1 and B1 tables below keep the numbers of the day they were written.

### Translation and Russian subtitles of T2 (operational, before C1)

- **What:** `translation-v1` (`claude-sonnet-5-5`) on the Sonnet-corrected T2
  fragment (B1, Gemini input), then `.srt` in Russian and corrected English,
  for the owner to watch; and the whole pipeline on the full T2 talk.
- **First run and a validator change:** the first translation of the fragment
  (0.084 USD) failed block 1 twice: two segments had compressed a repeated
  "Claude Code" and "GitHub" away, and the validator rejected any segment
  missing a kept glossary term. That check went beyond the spec (which asks
  for agreed glossary translations) and contradicts the prompt's compression
  rule, so a missing kept term is now a warning in
  `meta.translation.warnings` (daec62b). Rerun: 0 failed blocks, 1 warning
  ("GitHub" dropped in segment 35), 0.054 USD. Outputs `work/T2/T2.ru.json`,
  `work/T2/T2.ru.srt` (161 cues, 53% above 17 cps, max 30.0 cps, 3 shorter
  than 1 s) and `work/T2/T2.en.srt` (204 cues, 86% above 17 cps).
- **Full talk** (`talk-autopsy run data/test-set/T2.source.mp4 --lang ru
  --steps stt,align,fix,translate,srt --engine gemini+align
  --gemini-cache-only --glossary data/test-set/T2.glossary.md`, 27:52):
  estimate printed first 0.538 USD; spent 0.319 USD (correction 0.166, 6
  blocks, 33 segments changed, 0 failed; translation 0.154, 6 blocks, 0
  failed, 1 warning). Wall time 20.6 min: stt 892 s (two chunks from the
  cache, one by faster-whisper), alignment 170 s, correction 73 s,
  translation 95 s. Claude cost per hour of video: about 0.69 USD. Outputs
  `work/T2.source/T2.source.ru.srt` (447 cues, 54% above 17 cps) and
  `T2.source.en.srt` (531 cues). Without the cache, Gemini for the whole talk
  would have added about 27 min (A5 (setup) smoke test).
- **A defect found in the full run:** in the faster-whisper chunk an audience
  question came out garbled; correction rebuilt it by pulling words from the
  next segment, with a reason, so the validator let it
  pass. The segment's 1.3 s then carries a whole sentence: the two fastest
  cues of both SRTs (77 and 85 cps). This breaks the prompt's rule never to
  move words between segments; Backlog (validator: a segment that grows by
  far more than its neighbour shrinks).
- **For C1 (the owner's blind rating, later):** `work/T2/C1-rating.md` holds 20
  segments drawn with a fixed seed, English and Russian side by side, a 1–5
  field each. A second translation to compare against comes in a later
  session; until then it is a rating of one system, not a blind comparison.

### B1: correction-v1 on Gemini and faster-whisper transcripts

- **Hypothesis:** Claude correction with the video's glossary brings term
  accuracy to the stage 2 target (>= 85%) without changing words that were
  already right (false-edit rate near 0) and without raising WER. (The
  project plan is not in the repository; the wording follows the owner's
  instructions and the stage 2 gate.)
- **Run:** 2026-11-09, `talk-autopsy fix` (commit 2359042's code),
  prompt `correction-v1` (`talk_autopsy/prompts/correction-v1.md`),
  `claude-sonnet-5-5`, default effort and adaptive thinking, blocks of about
  5 minutes (2 per fragment) with 3 read-only context segments, the reviewed
  glossary in the cached prefix, no video context (the test-set fragments
  have no `meta.json`). Inputs: `work/T<n>/aligned.gemini.json` (A5 (setup):
  Gemini text, aligned times) and `work/T<n>/raw.whisper-large-v3.json`
  (A1 (setup)). Scored with `eval --raw <input>`.
- **Numbers:**

  | | T2 Gemini | T2 faster-whisper | T1 Gemini | T1 faster-whisper |
  |---|---|---|---|---|
  | term accuracy, input -> corrected | 93.46% -> 96.26% | 61.68% -> 94.39% | 99.15% -> 99.15% | 55.08% -> 100.00% |
  | WER, input -> corrected | 2.34% -> 2.14% | 6.19% -> 3.12% | 1.41% -> 1.41% | 11.20% -> 3.24% |
  | false-edit rate | 0.00% (0 of 2022) | 0.00% (0 of 1950) | 0.00% (0 of 1193) | 0.00% (0 of 1091) |
  | segments changed / edits | 3 / 3 | 34 / 34 | 0 / 0 | 51 / 51 |
  | failed blocks | 0 | 0 | 0 | 0 |
  | tokens in / cache write / cache read / out | 3,884 / 1,862 / 1,862 / 4,716 | 3,948 / 0 / 3,724 / 6,179 | 2,636 / 1,793 / 1,793 / 3,092 | 2,785 / 0 / 3,586 / 5,354 |
  | cost | 0.060 USD | 0.070 USD | 0.041 USD | 0.060 USD |
  | estimate before the run | 0.092 USD | 0.092 USD | 0.060 USD | 0.061 USD |

  The second run on each fragment read the whole prefix from the cache (same
  prompt and glossary within five minutes). Output tokens include adaptive
  thinking.
- **Conclusion:** the hypothesis holds on all four inputs. The largest gain is
  on faster-whisper's T2 transcript: every "Quadcode" became "Claude Code"
  (product name in the glossary), plus `tmux`, `macOS`, `daltonized`,
  `at-mention`, `codebase`, `LLM`. What the correction did not do: turn spoken
  slash commands and key names into their written form on faster-whisper input
  ("slash theme" stays; 6 terms of T2 missed), and on Gemini input
  `Shift+Enter`, `at-mention`, `PR`, `Command+F` stayed as Gemini wrote them
  ("shift-Enter", "@-mention", "pr", "Cmd+F"). WER fell together with the term
  fixes; no correct word was changed. Biases: the glossary was reviewed
  against the reference (the model was told which terms occur); T1's
  reference was decided by a Claude model by rules close to the prompt's, so
  T1 is secondary evidence; the false-edit rate counts words, not meaning,
  and the owner's check of the T2 edits (B2) is pending in
  `work/T2/B2-review.md` (3 edits, with time codes and the reference text).
- **Decision:** keep correction-v1 as the stage 2 baseline. The hold-out T6 and
  the term target are judged at the gate, not here.

### B4: Opus instead of Sonnet for correction-v1

- **Hypothesis:** the larger model makes more of the needed term fixes on the
  same input, at a higher cost.
- **Run:** 2026-11-09, as B1 on `work/T2/aligned.gemini.json` only, with
  `--model claude-opus-5-5` (default effort `medium`, adaptive thinking).
- **Numbers:**

  | | Sonnet 5.5 (B1) | Opus 5.5 |
  |---|---|---|
  | term accuracy | 96.26% (103/107) | 100.00% (107/107) |
  | WER | 2.14% | 2.00% |
  | false-edit rate | 0.00% | 0.00% |
  | segments changed | 3 | 6 |
  | output tokens | 4,716 | 5,169 |
  | cost | 0.060 USD | 0.129 USD |

  Opus made Sonnet's three edits plus `Shift+Enter`, `at-mention`,
  `Command+F` and `PR`.
- **Conclusion:** on this one input Opus reached every glossary term at 2.2x
  the cost; Sonnet left four spellings that Gemini had written in a common
  but non-glossary form. One run per model on one fragment: no variance
  estimate, and the gain is four term occurrences.
- **Decision:** Sonnet stays the default (`config.py`; the owner reserved Opus
  for experiments, 2026-11-09). Whether a prompt change gets
  Sonnet to the same spellings is a correction-v2 question; Backlog.

### A5 (setup): forced alignment of Gemini transcripts

- **Hypothesis:** none yet; this is the setup the owner's direction requires.
  Gemini's timestamps are model estimates (A4 (setup): 8 T2 segments end past
  the audio), so Gemini text can drive subtitles only after its words are
  placed on the audio by a forced aligner.
- **Choice of aligner (checked 2026-11-09):** torchaudio's `forced_align` and
  its MMS pipeline are deprecated since torchaudio 2.8 (the docs say "removed in
  2.9"; torchaudio's last release is 2.11.1 while torch is at 2.15, so its op
  library is built for an older torch); the MMS forced-alignment weights used by
  torchaudio's `MMS_FA` and by `ctc-forced-aligner` are CC-BY-NC 4.0, and
  `ctc-forced-aligner`'s last release (1.0.2, Feb 2025) states no Python 3.13
  support; WhisperX pulls a large dependency tree. Chosen: torchaudio's
  `WAV2VEC2_ASR_BASE_960H` (wav2vec 2.0 base fine-tuned on LibriSpeech 960 h,
  English characters, Apache-2.0, 360 MB; the model WhisperX uses for English)
  for the per-frame character probabilities only, with the Viterbi search
  written in numpy in `talk_autopsy/align.py`, so nothing depends on the
  deprecated op. torch 2.11.0+cpu and torchaudio 2.11.1+cpu (CPU wheels, about
  110 MB download) are the optional `align` extra; download total about
  0.5 GB including the model, under the 1.5 GB limit. Tests never load them.
- **Method:** the audio is run through the model in 30 s pieces (20 ms
  frames). Segments are grouped into blocks of about 900 characters (about a
  minute); each block is searched in an audio window that starts at the end of
  the previous block's last word and is 1.5x the block's estimated length plus
  20 s, with a "garbage" state before and after the text (scored as each
  frame's best label), so the block finds its place and the input's time
  estimates are used only for block length, never for position. Digits are
  spelled out, `&` `@` `%` `+` become words, other symbols are dropped; a word
  with nothing left keeps interpolated times and is listed as unplaced.
- **Run:** 2026-11-09, on the existing `work/T<n>/raw.gemini.json` (A4 (setup),
  not a new speech-recognition run), 8 CPU threads, nothing else running.
  Outputs `work/T<n>/aligned.gemini.json`, numbers in `work/A5-setup.json`.
- **Numbers:**

  | | T2 | T1 |
  |---|---|---|
  | audio | 600.0 s | 566.9 s |
  | wall time (model already loaded) | 69.9 s | 55.1 s |
  | of which acoustic model | 66.7 s | 53.6 s |
  | realtime factor | 8.6x | 10.3x |
  | blocks / failed / low probability | 12 / 0 / 0 | 8 / 0 / 0 |
  | words / unplaced | 2067 / 0 (0.0%) | 1206 / 0 (0.0%) |
  | words matched with faster-whisper (same token in the same place) | 1940 | 1083 |
  | start-time difference to faster-whisper, median | 130 ms | 230 ms |
  | 95th percentile | 410 ms | 660 ms |
  | within 100 ms / 250 ms | 25.1% / 82.7% | 7.5% / 58.7% |
  | signed median (aligner minus Whisper) | +130 ms | +230 ms |
  | differences over 1 s | 10 | 7 |

  The first model download and load took 96 s.
- **Conclusion:** every word of both Gemini transcripts gets a measured time,
  about ten times faster than real time on this laptop. The comparison is
  with faster-whisper's own word times, which are **a proxy, not a
  hand-labelled reference**: the two disagree by a steady offset (the aligner
  puts word starts 130–230 ms later; the signed and the absolute medians are
  equal), not by scatter, and which one is closer to the true onset is not
  measured. Whisper's word timestamps come from cross-attention and are known
  to run early; a CTC aligner marks the frame where a character becomes the
  most probable label, which can run late. The few differences over a second
  are short words ("and", "so", "the") where the two transcripts' word
  sequences match at a neighbouring occurrence.
- **Decision:** the aligner is used for the `gemini+align` engine. Timing drift
  against hand-labelled onsets (metrics `timing_drift`) remains for stage 3;
  Backlog.

**Smoke test on a full talk (operational, not scored).** `gemini+align` on the
whole T2 source, 27:52 (1672.5 s of audio extracted from
`data/test-set/T2.source.mp4`); no `eval`, no benchmark row; outputs in
`work/T2.source/`. Chunk plan: cuts at 580.1 s and 1187.8 s (the longest
silences near the 10-minute marks), chunks of 580, 608 and 485 s.

| Attempt | What happened |
|---|---|
| 1 (20:00) | chunk 1 on `gemini-3.8-flash`: 104 segments, 749 s. Chunk 2: `gemini-3.8-flash` answered 503 on all attempts (waits 20, 60, 120 s), then `gemini-3.6-flash` the same; at 1405 s the chunk went to faster-whisper, as designed. Stopped by hand: a second faster-whisper decode would have spoiled the A2 timing running at the same time. Chunk 1 stayed cached. |
| 2 (20:38) | chunk 1 from the cache in 0.6 s. Chunk 2 on `gemini-3.8-flash`: 108 segments, 411 s, no retry. Chunk 3: **429 RESOURCE_EXHAUSTED with a per-day quota**; the run stopped with the message that finished chunks are cached, as designed (12.4 min). No further Gemini call was made that day (the day's rule). |
| 3 (20:56, the pipeline run) | `run --gemini-cache-only` (added for this case): chunks 1–2 from the cache, chunk 3 by faster-whisper large-v3 on 8 threads, 887 s for 485 s of audio (0.55x), not cached. Then forced alignment of all 1672 s. |

| | Value |
|---|---|
| Gemini time per chunk (one call each, no retry) | 749 s for 580 s of audio; 411 s for 608 s |
| Gemini time per hour of audio, from those two chunks | about 59 min (1160 s for 1188 s of audio) |
| retries / fallbacks used | attempt 1: 6 failed calls, model fallback, faster-whisper fallback; attempt 2: none before the quota stop |
| forced alignment, whole talk | 165 s for 1672 s of audio (10.1x real time), 30 blocks, 0 failed, 0 low-probability, 5483 words, 2 unplaced (0.04%) |
| alignment time per hour of audio | about 6 min |
| total per hour of audio (Gemini + alignment, no retries) | about 65 min, against the plan's limit of 30 min per hour of video |

Conclusion: the path works end to end on a real talk, and the cache, the
model fallback, the per-chunk faster-whisper fallback and the quota stop all
fired on real errors on the first day. It does not meet the time limit:
Gemini on the free tier takes about as long as the audio, chunks run one after
another, and 503 retries add minutes. The free daily quota did not last
through one 28-minute talk on a day that had already used Gemini for the test
set (A4 (setup): about ten calls). Backlog: chunks in parallel, within the
free tier's rate limits.

The subtitle writer (`talk_autopsy/subtitles.py`, `talk-autopsy srt`) was
built ahead of stage 3 as a viewing tool, so the owner can watch the output;
hypothesis D1 (compression for reading speed) is measured in stage 3. From the
smoke-test output, uncorrected English: `work/T2.source.en.srt`, 530 cues, 84%
of them above 17 characters per second (verbatim English of a fast speaker),
20 cues shorter than 1 s.

### A2: faster-whisper with the glossary as initial prompt (and 8 CPU threads)

- **Hypothesis:** the glossary as Whisper's `initial_prompt` raises term
  accuracy by at least 20 points over the run without it. The same pair also
  measures speed with `cpu_threads` = 8 (the logical CPUs) against the
  4-thread runs of A1 (setup); thread count by itself does not change the
  decoded text.
- **Run:** 2026-11-09, faster-whisper 1.2.1, large-v3, int8, CPU, beam 5,
  VAD on, `--glossary-prompt` (the reviewed glossary joined with ", ", capped at
  200 tokens; all 39 T2 and 37 T1 terms fit), `--threads 8` (new option,
  recorded as `meta.cpu_threads`). T2 then T1, nothing else heavy on the CPU
  except a few minutes of light editing and tests; a second faster-whisper
  process started for about one minute near the end of the T1 run (a chunk
  fallback in the smoke test, stopped at once). Test-set runs 7 and 8 of 10.
- **Numbers** (reviewed glossaries; baseline = A1 (setup) re-scored):

  | | T2 baseline | T2 prompt | T1 baseline | T1 prompt |
  |---|---|---|---|---|
  | term accuracy | 61.68% (66/107) | 51.40% (55/107) | 55.08% (65/118) | 96.61% (114/118) |
  | WER | 6.19% | 6.43% | 11.20% | 4.32% |
  | substitutions / deletions / insertions | 60 / 42 / 25 | 85 / 26 / 21 | 67 / 47 / 21 | 17 / 3 / 32 |
  | segments | 127 | 54 | 93 | 95 |
  | CPU threads | 4 | 8 | 4 | 8 |
  | transcription wall time | 1312 s | 3155 s | 1295 s | 1002 s |
  | realtime factor | 0.457 | 0.190 | 0.438 | 0.566 |

- **Conclusion:** the hypothesis holds on T1 (+41.5 points) and fails on T2
  (-10.3 points), the accented fragment the stage is about. On T1 the speaker
  says "Claude" clearly, and the prompt only settles the spelling (`CloudCode`
  -> `Claude Code` 42 times). On T2 the product name sounds like
  "Quad Code", and the prompt does not move Whisper off it (0/28 either way);
  it also made Whisper decode whole 30-second windows as single segments,
  write "code base" for `codebase` (0/7) and fall back to temperature
  sampling repeatedly, which is where the 53 minutes went (one window took
  about 25 minutes). `Excalidraw`, in the prompt, still came out as
  "Excaladrol" / "Excalibur" (0/3). The same biases as A3 apply: the glossary
  was reviewed against the reference, and T1's reference spells product names
  by a rule that matches the glossary.
  Speed: on T1, where the prompt barely changed the segmentation, 8 threads
  ran at 0.57x real time against 0.44x with 4 (1.29x faster). The laptop has 8
  logical CPUs (likely 4 physical cores), so doubling threads does not double
  speed. The T2 number is not a thread measurement: the decoding itself
  changed.
- **Decision:** not adopted for accented speech. Whisper stays the fallback
  engine (owner direction); if it is used, `--threads 8` is the faster
  setting. A one-hour talk at 0.57x takes about 1 h 45 min on this laptop,
  far over the plan's limit of 30 minutes per hour of video.

### A3: AssemblyAI with the glossary as key terms

- **Hypothesis:** passing the fragment's glossary to AssemblyAI as
  `keyterms_prompt` raises term accuracy over the run without it, without
  raising WER. (The project plan is not in the repository; the wording follows
  the owner's instructions of 2026-11-09.)
- **Run:** 2026-11-09, engine code at 0505ba9 (unchanged), Universal-3.5 Pro,
  `--glossary-prompt` with the reviewed glossaries (39 terms on T2, 37 on T1;
  none over 6 words), otherwise as A3 (setup). Outputs
  `work/T<n>/raw.assemblyai+keyterms.json`. Test-set runs 9 and 10 of 10 in
  stage 1 (A2's two runs are 7 and 8).
- **Numbers** (reviewed glossaries; baseline = A3 (setup) transcripts re-scored):

  | | T2 baseline | T2 keyterms | T1 baseline | T1 keyterms |
  |---|---|---|---|---|
  | WER | 6.97% | 4.43% | 1.41% | 0.33% |
  | substitutions / deletions / insertions | 37 / 95 / 11 | 17 / 66 / 8 | 11 / 4 / 2 | 3 / 0 / 1 |
  | term accuracy | 80.37% (86/107) | 99.07% (106/107) | 95.76% (113/118) | 100.00% (118/118) |
  | wall time | 39 s | 27 s | 35 s | 25 s |
  | cost at list price | 0.035 USD | 0.043 USD | 0.033 USD | 0.041 USD |

  The keyterms runs cost $0.26 per hour of audio ($0.21 + $0.05 for keyterms,
  pricing page); 0.084 USD for both, from the free credit.
- **Conclusion:** on these numbers the hypothesis holds: +18.7 points of term
  accuracy on T2, +4.2 on T1, and WER fell too. Three biases make the size
  untrustworthy. (1) The glossary was reviewed against the reference so that
  every term occurs in it: the engine was told exactly which rare words to
  expect, which a real run on an unseen video does not get (its glossary would
  be drafted from a first transcript and include terms that never occur).
  (2) AssemblyAI helped build both references (methodology, bias caveat), so
  its WER is biased low; T1's 0.33% mostly says that T1's reference agrees with
  AssemblyAI. (3) On T2 most remaining errors are deletions (66): AssemblyAI
  drops repetitions and false starts that the verbatim reference keeps.
- **Decision:** recorded as `assemblyai+keyterms`. Whether a keyterms run on an
  unseen video keeps the gain needs a glossary drafted without the reference
  (T3–T5); Backlog.

### A4 (setup): Gemini transcripts of T2 and T1

- **Hypothesis:** none. These transcripts are one of the three inputs of the
  consensus reference; no quality claim is made here (see the bias caveat in the
  methodology section).
- **Run:** 2026-11-09, engine code at deefb6d. `google-genai` 3.0.0, model
  `gemini-3.8-flash` (the most capable model on the free tier; `gemini-4-argon`
  is paid only), WAV uploaded through the Files API, prompt for a verbatim
  English transcript as a JSON array of `{start_seconds, end_seconds, text}`
  (structured output), no glossary context, default thinking settings,
  `max_output_tokens=32768`. Free tier. No quota rejection, so the
  `gemini-3.6-flash` fallback never ran. Outputs `work/T<n>/raw.gemini.json`.
- **Numbers:**

  | | T2 | T1 |
  |---|---|---|
  | audio | 600.0 s | 566.9 s |
  | failed calls (503 UNAVAILABLE, "high demand") | 1, plus one earlier run that failed on its only call (before retries existed) | 2, plus one earlier run that failed after 4 attempts |
  | wall time, upload to answer, retry waits included | 446 s | 602 s |
  | segments | 109 | 79 |
  | segments whose estimated end is past the end of the audio | 8 (last ends at 640 s) | 0 |
  | tokens: prompt / output / thinking | 15,218 / 7,148 / 0 | 14,390 / 4,783 / 0 |
  | cost (free tier) | 0 USD | 0 USD |
  | cost at paid prices ($0.75 / $3.75 per million, checked 2026-11-09) | 0.038 USD | 0.029 USD |
  | normalized words | 2066 | 1206 |
  | backbone words matched (agreement with faster-whisper) | 1940 of 2035 (95.3%) | 1083 of 1179 (91.9%) |

  The prompt token counts imply about 25 audio tokens per second, not the 32 the
  docs state. Gemini's timestamps are model estimates (`meta.timestamps`) and
  are not used anywhere.
- **Conclusion:** the engine produces a complete verbatim transcript of both
  fragments. `gemini-3.8-flash` was often overloaded (503 on 8 of 10 calls today);
  one generation takes about 6–7 minutes.
- **Decision:** the transcripts go into the adjudication of T2 and T1. A4 (the
  Gemini hypothesis) is measured against the finished reference, with the bias
  caveat. Not recorded in `results/benchmark.csv`: no metric yet.

### A3 (setup): AssemblyAI transcripts of T2 and T1

- **Hypothesis:** none, as A4 (setup).
- **Run:** 2026-11-09, engine code at 0505ba9. `assemblyai` SDK 1.8.2,
  `speech_models=["universal-3-5-pro"]` (the model that ran is recorded and was
  the one requested), `language_code="en"`, speaker labels off, no keyterms.
  Segments are the service's sentences. Outputs `work/T<n>/raw.assemblyai.json`.
- **Numbers:**

  | | T2 | T1 |
  |---|---|---|
  | audio (billed duration; the API reports no separate billed field) | 600 s | 567 s |
  | wall time, upload to result | 39.4 s | 35.3 s |
  | segments (sentences) | 149 | 79 |
  | cost at list price ($0.21 per hour, pricing page dated 2026-10-27) | 0.035 USD | 0.033 USD |
  | normalized words | 1968 | 1203 |
  | backbone words matched (agreement with faster-whisper) | 1869 of 2035 (91.8%) | 1089 of 1179 (92.4%) |

  Both costs are drawn from the account's free credit.
- **Conclusion:** the engine runs end to end, about 15x faster than real time.
- **Decision:** the transcripts go into the adjudication; A3 is measured against
  the finished reference, with the bias caveat. Not recorded in
  `results/benchmark.csv`: no metric yet.

Test-set runs in stage 1 so far: 6 of 10 (A1 (setup) x2, A3 (setup) x2,
A4 (setup) x2; the failed Gemini attempt produced nothing and is not counted).
Money: 0.07 USD at list price, all from free credit.

### A1 (setup): first faster-whisper run on T1 and T2

- **Hypothesis:** none. This run produces the drafts for the reference transcripts
  and measures time; it is not a test of quality.
- **Run:** 2026-11-09, engine code at 35d8efc. faster-whisper 1.2.1, model
  `large-v3` (`Systran/faster-whisper-large-v3`), `device=cpu`,
  `compute_type=int8`, `beam_size=5`, `vad_filter=True`, `word_timestamps=True`,
  `language=en`, no initial prompt. Laptop with 8 logical CPUs; faster-whisper's
  default of 4 CPU threads. T2 ran first, T1 directly after it; outputs
  `work/T<n>/raw.whisper-large-v3.json` (local).
- **Download:** 3.09 GB (`model.bin` 3,087,284,237 bytes plus tokenizer and
  config) into the Hugging Face cache, 292 s.
- **Numbers:**

  | | T2 | T1 |
  |---|---|---|
  | audio | 600.0 s | 566.9 s |
  | audio after VAD | 546.6 s | 525.8 s |
  | model load | 19.7 s | 16.4 s |
  | transcription (wall) | 1311.9 s | 1295.1 s |
  | realtime factor (audio s / transcription s) | 0.457 | 0.438 |
  | whole command (wall, incl. Python start-up) | 1334 s | 1315 s |
  | segments | 127 | 93 |
  | segment length, mean / median / max | 4.50 / 4.16 / 9.87 s | 5.09 / 4.36 / 16.90 s |
  | words per segment, mean / max | 16.0 / 22 | 12.7 / 32 |
  | segments with confidence < 0.5 | 0 | 0 |
  | distinct confidence values | 19 | 19 |
  | words with probability < 0.5 | 62 of 2048 (3.0%) | 47 of 1193 (3.9%) |
  | segments with no word (only `.`) | 0 | 2 (the last two) |

  Confidence is `exp(avg_logprob)`. faster-whisper computes `avg_logprob` once
  per 30-second decoding window, so all segments of a window share it (19 values
  for 127 and 93 segments). "No segment below 0.5" therefore says little about
  single segments; word probabilities are the per-word signal.
- **Quality:** no numbers. There is no reference transcript yet, so WER and term
  accuracy cannot be computed; the drafts are not looked at for quality here.
- **Conclusion:** the engine runs end to end on both fragments. On this laptop
  large-v3 int8 runs at about 0.45x real time: a 10-minute fragment takes about
  22 minutes. A 30-second smoke clip earlier the same day ran at 0.95x, so speed
  on short clips does not predict speed on a full fragment (see Backlog).
- **Decision:** drafts `T1.draft.txt` and `T2.draft.txt` go to the owner for
  proofreading; the raw-Whisper baseline (WER on T2) is measured once the
  reference exists. Not recorded in `results/benchmark.csv`: no metric yet.

## Backlog

Ideas that come up during a stage and wait for a later one.

- Burned-in MP4 on a re-run (v1.0 cache check, 2026-11-11): `outputs.write_outputs` re-encodes
  `<stem>.<lang>.mp4` every time (0.27x real time), even when the `.ass` and the video did not
  change. Skip it when the existing file is newer than both (outputs.py, not frozen).

- **Correction on the main path** (stage 2 close, 2026-11-11): it stays in
  `pipeline-v3` at about 0.39 USD per hour of video (0.0645 USD per 10
  minutes on T6), for one correct edit on T6 (`genetic` -> `agentic`) and 0
  edits in the four T1/T2 repeats; its measured value is on fallback-engine
  input with correction-v1 and a reference-reviewed glossary (B1); the
  shipped correction-v2 was never run on fallback input. Revisit after T3: drop it from the AssemblyAI path, or run it
  only when a fallback engine produced the transcript.

- ~~Speed of faster-whisper on this laptop (A1)~~ Closed 2026-11-09 by A2:
  `cpu_threads` 8 gives 0.57x real time on T1 against 0.44x with the default
  4 (1.29x). A one-hour talk still takes about 1 h 45 min; Whisper is the
  fallback engine, not the main one. Decoding fallbacks (temperature retries)
  can make one window take many minutes (A2 on T2); not pursued further.
- Segment confidence (A1): faster-whisper's `avg_logprob` is per 30-second
  window. For flagging doubtful segments for correction, use word probabilities
  (for example the minimum or mean per segment) instead.

- Words moved between segments (operational runs, 2026-11-09): correction pulled words from
  the next segment into a garbled one; the edit had a reason, so the validator
  passed it. Add a check that a segment does not gain words its neighbour
  loses, and say in correction-v2 that a garbled segment is left as it is.
- Gemini chunks in parallel (A5 (setup) smoke test): about 65 min per hour of
  audio against the 30-minute limit, because chunks run one after another;
  measure two or three at a time against the free tier's rate limits.
- correction-v2 candidate (B1/B4, 2026-11-09): Sonnet keeps Gemini's
  "shift-Enter", "@-mention", "Cmd+F", "pr" and faster-whisper's spoken
  "slash theme"; Opus fixes the first four. A prompt line asking to write
  terms exactly as the glossary spells them, including key combinations and
  slash commands said aloud, may close the gap; measure on T2 only after the
  owner's B2 check.
- Timing reference for A5 / D-hypotheses (2026-11-09): label word onsets by
  hand for a few minutes of T1 and T2 so the aligner's +130–230 ms offset
  against Whisper can be attributed to one of them; then decide whether to
  shift aligned times by a constant.
- Glossary leak in A2 and A3 (2026-11-09): both runs pass a glossary that was
  reviewed against the reference, so every term is known to occur. On T3–T5,
  measure the prompt or keyterms with a glossary drafted from a first
  transcript only, the way an unseen video would get it.
- Term matching and possessives (glossary review, 2026-11-09): `Anthropic`
  occurs in T1 only as "Anthropic's", which `metrics.term_accuracy` does not
  count, so the term is excluded as absent. Accepting `'s` on the last token,
  like the regular plural, would count it; changing the metric rescores every
  row, so it waits for a stage boundary.
- ~~T1 reference spells the GitHub mention "@Claude"~~ Closed 2026-11-09:
  spans 0052 and 0055 set to `@claude` on the owner's instruction, T1
  re-scored (see "T1 re-scored after the `@claude` fix").

- translation-v2 still calques a job title (T2 segment 2: rendered word for
  word) although the prompt asks for job titles by meaning (C1 blind run, 2026-11-09). Revisit after the owner's
  blind scores; a third translation prompt version is within the limit of 5.
  The owner scored that item 5 for both versions (C1 blind, 2026-11-10); no
  prompt change before the freeze.

- Rejected answers are not saved (operational runs, 2026-11-09): when a block
  fails validation twice only the problem list is logged, so the cause had to
  be inferred from the glossary. Save the rejected answer next to the output
  (`<output>.rejected.json`).
- Stamps do not include the code version (operational runs): a validator fix
  did not invalidate `fixed.json`, and its stamp had to be deleted by hand.
  Add the prompt file hash and a validator version to the fix and translate
  stamps.
- Short sentence cues (operational runs): AssemblyAI segments by sentence, so
  many cues are one short sentence that cannot linger; 45 Russian cues of
  real-01 are shorter than 1 s. Joining consecutive short segments into one
  cue (still never splitting a segment) would lower the reading speed
  without cutting text; measure in stage 3 (D-hypotheses). Still the
  fastest cues after D1 (setup): sentence units do not merge them, since
  each ends a sentence (T2: "Great." on 0.11 s at 70.8 cps).
- Cue timing inside a unit (D1 (setup), 2026-11-10): mapping a target cue to
  the English by character share instead of word count lowered the share
  above 25 cps by up to 1.3 points; snapping to English clause ends costs up
  to 1.0 point. A display-time rule that evens reading speed across the
  cues of one unit (within the span its English words allow) may do more.
  Subtitle layout, not frozen.
- Very short cues from word timing (stress-01, 2026-11-10): a clause that
  opens a unit can map onto one short English word and get 0.16–0.22 s on
  screen (the two fastest Russian cues of stress-01, 58.0 and 43.5 cps). A
  minimum share of the unit's time per cue, or spreading the time of a unit
  in proportion to characters within the span its English words allow,
  would fix it. Subtitle layout, not frozen.
- Speaker labels (stress-01): the interview's fast cues cannot be attributed
  to a speaker without diarization; AssemblyAI's speaker labels are off.
  v1.1 idea, not implemented.
- Idiom calque in a unit (D1 (setup)): an idiom about editor loyalty
  came out word for word in the sentence-unit translation of the C1 input,
  by meaning in the per-segment one. One sample; watch in the T6 run.
- Translation warnings for kept terms (operational runs): translation-v2
  dropped a person's name, "commit", "tokens" in a few segments (3 warnings per
  video). With no compression rule left, check whether these are pronoun
  substitutions or real losses before the C1 decision.

- ~~Server-side refusal fallback on the Claude calls~~ Decided 2026-11-09:
  on in `run`, off in experiments (decisions).
- Next-video failure modes (independent LLM review #1, finding 14; after the T6 run):
  diarization (speaker labels are off, so an interview's turns are not
  attributed), music and long non-speech stretches (no detection; AssemblyAI
  and the auto-glossary see whatever is there), long videos (one glossary call
  over the whole first pass; untested above about 40 minutes).
- Hygiene (independent LLM review #1, finding 15): split `cli.py` (about 1,300 lines)
  by command group; the adjudication and judge tooling (`adjudicate.py`,
  `judge.py`, `ref_bounds.py`) is large for evaluation code and could share
  one span model. Not before the T6 run (frozen paths are not touched, and
  these are not frozen but the run uses them).
- One topic (independent LLM review #1, finding 5): T1, T2 and T6 are all about
  AI-assisted software development. T3 is chosen from a new domain after the
  T6 run, to measure transfer the T6 result cannot show.
- ~~AssemblyAI key-term limit~~ Fixed 2026-11-11 in `pipeline-v3`
  (`select_keyterms`). (2026-11-10; frozen path, no code
  change before the T6 run): `stt/assemblyai.py` raises an error when the
  key-term list is above the service limit instead of truncating it. Fix
  after the T6 run: truncate with the user's terms first, then the
  auto-glossary's, and report how many were dropped.
- **translation-v4 candidate** (C1b rating, 2026-11-11; not now: after the
  T6 run and within the DoD limit of 5 translation-prompt versions): keep
  v3's term classes, but glosses of at most about 3 words; no expansion of
  acronyms (REPL, CI, API); established jargon the target viewer already
  uses (prompt, tokens, thinking tokens, alignment, computer science) is
  `keep`; idioms by meaning. Needs a new blind
  rating against v2 before it can replace it.
- UI element names in translation (owner, 2026-11-10): names of UI elements
  are translated by meaning, not by their official localized name (Windows
  "Dictation" was rendered with a literal Russian word, while the Russian
  Windows UI calls it "Голосовой ввод"). Idea for a future translation
  version: keep UI element names in English with a short Russian gloss in
  brackets. *Addressed 2026-11-10 by translation-v3 (the `ui` class, C1b), an
  option until the owner's blind rating. On T2 the model wrote "Dictation
  (диктовка)" as a UI name; the classifier had put `dictation` in translate,
  and the gloss is not the localized name: a user glossary entry with policy
  `ui` and the official name fixes it per video.*
