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

## Summary

| ID | Date | Hypothesis | Run | Numbers | Conclusion | Decision |
|---|---|---|---|---|---|---|
| A3 | 2026-11-09 | glossary as AssemblyAI keyterms raises term accuracy | Universal-3.5 Pro + keyterms (reviewed glossaries); T2, T1 | T2 term accuracy 80.4% -> 99.1%, WER 6.97% -> 4.43%; T1 95.8% -> 100%, 1.41% -> 0.33%; 0.084 USD | holds on these numbers; size inflated by a glossary built from the reference and by reference bias | recorded; re-test with a reference-free glossary on T3–T5 |
| A4 (setup) | 2026-11-09 | none: Gemini transcripts for the consensus reference | gemini-3.8-flash, Files API, structured JSON, no glossary; T2, T1 | 446 s / 602 s wall incl. 503 retries; 0 USD (free tier; 0.038 / 0.029 USD at paid prices); agreement with the backbone 95.3% / 91.9% | runs end to end; frequent 503 "high demand" | transcripts feed the T1 and T2 adjudication |
| A3 (setup) | 2026-11-09 | none: AssemblyAI transcripts for the consensus reference | Universal-3.5 Pro, no keyterms; T2, T1 | 39 s / 35 s wall; 0.035 / 0.033 USD list price (free credit); agreement with the backbone 91.8% / 92.4% | runs end to end, about 15x faster than real time | transcripts feed the T1 and T2 adjudication |
| A1 (setup) | 2026-11-09 | none: first run of the engine, for timings and drafts | faster-whisper large-v3, int8, CPU, no prompt; T1, T2 | about 22 min per 10-min fragment (realtime factor 0.44–0.46); no quality numbers | runs end to end; slower than real time on this laptop | drafts go to the owner for proofreading (superseded: the transcripts are the backbone of the consensus reference) |

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

- Speed of faster-whisper on this laptop (A1): full fragments ran at 0.44–0.46x
  real time, a 30-second clip at 0.95x. Candidates: `cpu_threads` (4 by default
  of 8 logical CPUs), power or thermal limits on long
  runs, and decoding fallbacks on long audio. Matters for stage 3 (a one-hour
  talk would take over two hours), not for the stage 1 gate.
- Segment confidence (A1): faster-whisper's `avg_logprob` is per 30-second
  window. For flagging doubtful segments for correction, use word probabilities
  (for example the minimum or mean per segment) instead.

- Glossary leak in A2 and A3 (2026-11-09): both runs pass a glossary that was
  reviewed against the reference, so every term is known to occur. On T3–T5,
  measure the prompt or keyterms with a glossary drafted from a first
  transcript only, the way an unseen video would get it.
- Term matching and possessives (glossary review, 2026-11-09): `Anthropic`
  occurs in T1 only as "Anthropic's", which `metrics.term_accuracy` does not
  count, so the term is excluded as absent. Accepting `'s` on the last token,
  like the regular plural, would count it; changing the metric rescores every
  row, so it waits for a stage boundary.
- T1 reference spells the GitHub mention "@Claude" (twice), against the
  reference convention `@claude`; the term is an identifier and matched
  case-sensitively, so it counts 0 occurrences and is excluded. Fix in
  `T1.adjudication.md` (type `@claude` in those spans) and `resolve T1 --force`
  — an owner edit; this session may not touch the adjudication file.

- Server-side refusal fallback on the Claude calls (the docs recommend it for
  Sonnet 5.5 and Opus 5.5); decide when the correction stage is written.
