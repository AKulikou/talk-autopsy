# Experiment log

Every measured run on the test set gets an entry here, newest first, and a row in
[results/benchmark.csv](../results/benchmark.csv). Decisions about scope and
process are in [decisions.md](decisions.md).

Entry format:

- **ID:** `<stage>-<n>`, for example `S1-01` for the first speech recognition
  hypothesis; a rerun of the same hypothesis keeps the ID with a suffix (`S1-01b`).
- **Hypothesis:** what is expected and why, written before the run.
- **Run:** fragments, engine or prompt version, models, settings, date, commit.
- **Numbers:** the metrics that the hypothesis is about, next to the baseline.
- **Conclusion:** what the numbers show, including when they show nothing.
- **Decision:** keep, revert, or try something else; anything new goes to the
  Backlog below, not into the current stage.

Limits (from CLAUDE.md): at most 3 runs per hypothesis; stage 1 at most 10
test-set runs and $5; stage 2 at most 10 correction-prompt and 5 translation-prompt
versions and $20; a cost estimate before every full-video run.

## Summary

| ID | Date | Hypothesis | Run | Numbers | Conclusion | Decision |
|---|---|---|---|---|---|---|
| A1 (setup) | 2026-11-09 | none: first run of the engine, for timings and drafts | faster-whisper large-v3, int8, CPU, no prompt; T1, T2 | about 22 min per 10-min fragment (realtime factor 0.44–0.46); no quality numbers | runs end to end; slower than real time on this laptop | drafts go to the owner for proofreading |

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
- Wordless segments (A1): T1 ends with two segments whose text is only `.`, at
  the end of the audio. Decide in stage 1 whether the engine drops segments with
  no word, which changes ids before any later stage sees them.
- Glossary terms are counted as whole tokens, so a plural (`diffs`, `pull
  requests`) does not match the singular term. Decide when the glossaries are
  reviewed whether a term may list variants.

- Server-side refusal fallback on the Claude calls (the docs recommend it for
  Sonnet 5.5 and Opus 5.5); decide when the correction stage is written.
