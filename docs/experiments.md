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

The two references were not decided the same way, and that changes what their
numbers can carry.

- **T2** (strong accent, `T2.ref.txt`, 2052 words): 114 spans. 19 were decided
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

## T6 protocol

T6 is the sealed hold-out of stage 2 (a workshop with a live demo, a
non-native English accent). Its 10-minute window was cut blind on
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
3. **One run.** The frozen pipeline (`talk-autopsy run` with the tagged code,
   the auto-glossary and no hand glossary) runs on T6 once. Its numbers are
   recorded whatever they are; a prompt change after seeing them starts a new
   version that T6 can no longer judge.

## Operational runs

Whole videos through the production `run` (not scored: no reference, no
benchmark row). Default path since 2026-11-09: AssemblyAI first pass on
compressed audio (`audio.flac`), auto-glossary (Haiku), AssemblyAI with the
glossary as key terms, correction-v2 and translation-v2 (Sonnet, refusal
fallback on), `.srt` + `.ass` in Russian and English. No `--context` and no
user glossary in either run. Laptop as in the stage 1 runs; network times
include upload.

| | T2.source (talk, strong accent) | real-01 (interview, native speakers) |
|---|---|---|
| duration | 27:52 (1673 s) | 38:30 (2310 s) |
| command | `run data/test-set/T2.source.mp4 --lang ru` | `run data/test-set/source-video-1.mp4 --name real-01 --lang ru` |
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
  `.mcp.json` <- "MCP JSON". real-01: `[removed]` <- "[removed]" (and a
  harmless `GPU` <- "GPUs").
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
  on the fast, accented talk and lower on the interview. Many fast cues are
  short sentences (AssemblyAI segments by sentence) that cannot linger
  because the next cue starts at once; cues never cross a segment boundary.
- **Outputs:** `work/T2.source/T2.source.ru.srt` / `.ass`,
  `T2.source.en.srt` / `.ass`; `work/real-01/real-01.ru.srt` / `.ass`,
  `real-01.en.srt` / `.ass`. The prompt-05 outputs of the Gemini run are in
  `work/T2.source.prompt05/`. Of the money: the first start of the T2.source
  run was stopped by hand after its upload had begun (the old outputs had
  not been backed up yet); its AssemblyAI pass (up to 0.098 USD) is counted
  as spent in the session total.

## Stage 1 summary

Speech recognition on T1 (native speaker, 9:27) and T2 (strong accent, 10:00),
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
| C1 (blind) | 2026-11-10 | translation-v2 scores higher than v1 and >= 4.0 in a blind pairwise comparison | owner rated items 1–15 of `C1-blind.md` (T2, Gemini+align input, correction-v1 English) | v1 4.40, v2 4.67 over 15 pairs; v2 better 8, v1 better 3, ties 4 | both above 4.0; v2 preferred; not fully blind | **C1 met; translation-v2 adopted** |
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
  clauses v1 dropped (segment 18 keeps "at the same time" as "за один раз").
  The job title in segment 2 is still the calque "[removed]" in
  both versions, despite the new rule (Backlog).
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
- **Conclusion:** both versions are above the 4.0 target; v2 wins more pairs
  than it loses (8 to 3). **C1 is met.**
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
- **Decision:** the auto-glossary is the glossary step of the pipeline (Part 7
  of the session prompt); its suggested translations are suggestions, not
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
  lose or distort meaning — segment 4 drops "at the same time" (whole features
  in one go). The cause in all three is the compression rule of translation-v1.
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
  question came out garbled ("How much have you used files?"); correction
  rebuilt it by pulling words from the next segment ("... used Claude Code for
  machine learning modeling and a..."), with a reason, so the validator let it
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
  project plan is not in the repository; the wording follows the session
  prompt and the stage 2 gate.)
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
| 2 (20:38) | chunk 1 from the cache in 0.6 s. Chunk 2 on `gemini-3.8-flash`: 108 segments, 411 s, no retry. Chunk 3: **429 RESOURCE_EXHAUSTED with a per-day quota**; the run stopped with the message that finished chunks are cached, as designed (12.4 min). No further Gemini call was made in the session (session rule). |
| 3 (20:56, the 3.6 pipeline run) | `run --gemini-cache-only` (added for this case): chunks 1–2 from the cache, chunk 3 by faster-whisper large-v3 on 8 threads, 887 s for 485 s of audio (0.55x), not cached. Then forced alignment of all 1672 s. |

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
  fallback in the 2.4 smoke test, stopped at once). Test-set runs 7 and 8 of 10.
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
  -> `Claude Code` 42 times). On T2 the accent makes the audio sound like
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
  the session prompt of 2026-11-09.)
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

- ~~Speed of faster-whisper on this laptop (A1)~~ Closed 2026-11-09 by A2:
  `cpu_threads` 8 gives 0.57x real time on T1 against 0.44x with the default
  4 (1.29x). A one-hour talk still takes about 1 h 45 min; Whisper is the
  fallback engine, not the main one. Decoding fallbacks (temperature retries)
  can make one window take many minutes (A2 on T2); not pursued further.
- Segment confidence (A1): faster-whisper's `avg_logprob` is per 30-second
  window. For flagging doubtful segments for correction, use word probabilities
  (for example the minimum or mean per segment) instead.

- Words moved between segments (3.6, 2026-11-09): correction pulled words from
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

- translation-v2 still calques the job title "[removed]"
  ("[removed]", T2 segment 2) although the prompt asks for job
  titles by meaning (C1 blind run, 2026-11-09). Revisit after the owner's
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
  without cutting text; measure in stage 3 (D-hypotheses).
- Translation warnings for kept terms (operational runs): translation-v2
  dropped "[removed]", "commit", "tokens" in a few segments (3 warnings per
  video). With no compression rule left, check whether these are pronoun
  substitutions or real losses before the C1 decision.

- ~~Server-side refusal fallback on the Claude calls~~ Decided 2026-11-09:
  on in `run`, off in experiments (decisions).
