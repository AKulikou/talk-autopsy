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

No experiments yet.

## Backlog

Ideas that come up during a stage and wait for a later one.

- Server-side refusal fallback on the Claude calls (the docs recommend it for
  Sonnet 5.5 and Opus 5.5); decide when the correction stage is written.
