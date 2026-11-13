# CLAUDE.md

Guidance for Claude Code when working in this repository. What the project
measured and why is in `README.md`, `docs/experiments.md` and `docs/decisions.md`.

## Project

Talk Autopsy turns an English technical talk (a YouTube link or a local file)
into subtitles in a target language, with every stage measured on a fixed test
set. Pipeline (`talk_autopsy/pipeline.py`, `run`):

```
ffmpeg audio.wav + audio.flac -> AssemblyAI first pass (raw.json)
  -> auto-glossary (Haiku) -> AssemblyAI with key terms (raw2.json)
  -> Claude correction (correction-v2, fixed.json) -> sentence units (units.py)
  -> Claude translation (translation-v2.1, <lang>.json) -> .srt + .ass, optional MP4
fallbacks: chunked Gemini + forced alignment, then faster-whisper
```

## Commands

```bash
uv sync                          # Python 3.13 venv from uv.lock (uv sync --extra align: torch for forced alignment)
uv run pytest                    # offline unit tests (fake Claude client, generated audio)
uv run ruff check .              # lint
pwsh scripts/check.ps1           # before every push: what CI runs, secrets cleared, plus the name check
uv run python scripts/check_names.py   # de-identification check (local list + committed patterns)
uv run talk-autopsy doctor       # ffmpeg, JavaScript runtime for YouTube, keys (names only)
uv run talk-autopsy run <url-or-file> --lang ru [--dry-run] [--output srt,ass|mp4-burned|all]
uv run talk-autopsy eval T2 --hyp <transcript>.json [--raw <raw>.json] [--record --engine <e> --stage <s>]
uv run talk-autopsy eval T6 --hyp <transcript>.json   # counts only: J/M references, bounds, verdict
uv run talk-autopsy --help       # all commands: cut, transcribe, align, glossary-auto, fix, translate, srt,
                                 # adjudicate, resolve, judge, judge-refs, judge-compare, glossary-ref, ...
```

`fix`, `translate` and `eval` are experiment commands (one named model per
benchmark row, no refusal fallback); `run` is the tool (refusal fallback on).
Keys live in `.env` (never committed): `ANTHROPIC_API_KEY`,
`ASSEMBLYAI_API_KEY`, `GEMINI_API_KEY` (fallback, judge),
`GOOGLE_TRANSLATE_API_KEY` (baseline only).

## Layout

- `talk_autopsy/` — `cli/` (typer; parsing and printing only; one module per
  command group, registered in `cli/__init__.py`), `user_glossary.py` (the run's
  user glossary, shared by CLI and web), `pipeline.py`,
  `config.py` (settings as functions, model IDs, prices; `load_environment` is
  called only by entry points), `segments.py` (the one JSON format), `stt/`
  (assemblyai, gemini, chunked, faster_whisper), `align.py`, `glossary.py`,
  `glossary_auto.py`, `correct.py`, `units.py`, `translate.py`, `subtitles.py`,
  `outputs.py`, `youtube.py`, `blocks.py`, `core/` (shared Claude request code),
  `prompts/<version>.md`.
- Evaluation: `metrics.py`, `evaluation.py` (both frozen), `adjudicate.py`,
  `judge.py`, `ref_bounds.py`, `ref_glossary.py`, `translation_check.py`,
  `translation_rating.py`, `baselines/google_translate.py`, `secret_registry.py`
  (every secret name; the tests use it).
- `tests/` — offline; `conftest.py` has the fake Claude client and a placeholder
  for every secret; `tests/js/` tests the page's DOM-free modules (scroll,
  subtitle choice) with Node's built-in runner, no package install (skipped
  without Node, required in CI).
- `data/test-set/` — only `README.md` and `*.glossary.md` are tracked; media and
  reference transcripts stay local.
- `docs/experiments.md` (experiment log, metrics, criteria, Backlog),
  `docs/decisions.md`, `docs/review-*.md`, `docs/prior-art.md`;
  `results/benchmark.csv` (one row per measured run).
- `scripts/` — `check.ps1` / `check.sh`, `check_names.py` +
  `identifying_patterns.txt`, evaluation scripts, `web_screenshots.py` (README
  pictures from replay, `docs/web.md`), `history_rewrite.py` (rules, rewrite and
  per-commit check for the history rewrite; names only from the local list),
  `windows/` (the owner's launchers).

## Rules

- **Frozen paths** (git tag `pipeline-v3`): `ingest.py`, `stt/`, `align.py`,
  `glossary.py`, `glossary_auto.py`, `correct.py`, `blocks.py`, `units.py`,
  `translate.py`, `segments.py`, `pipeline.py`, `config.py`, `core/`,
  `metrics.py`, `evaluation.py`, `ref_bounds.py`, the three prompts in use,
  `uv.lock`. A change needs a new tag and a new hold-out; check with
  `git diff --stat pipeline-v3 -- <paths>`. New ideas go to the Backlog.
  `pipeline-v3.1` (2026-11-12) is the one observability exception: progress
  events in `pipeline.py` and `core/structured_call.py`, requests and outputs
  byte-identical (`tests/test_golden_requests.py`, `docs/decisions.md`).
  **Cache-key rule** (since `pipeline-v3.2`, 2026-11-12): any other change to
  a frozen path needs (a) golden tests unchanged, (b) the T2 re-run from cache
  at 0 USD, (c) the 22 files of `work/T2-v3-cache/` byte-identical; else
  revert it. Modules in `pipeline.STEP_CODE` (e.g. `metrics.py`) cannot pass.
  **Bugs are not frozen** (owner, 2026-11-12, `pipeline-v3.3`): a bug in a
  frozen module is fixed with a failing test committed first, the golden
  request snapshots unchanged, every cached T2/T6 answer getting the same
  verdict offline, no paid test-set re-run, a new tag and a decisions entry;
  a moved cache key is carried over by a one-off local script. A fix that
  changes what is sent to the models is not made. Improvements stay frozen.
  `pipeline-v3.4` (2026-11-13): a block rejected twice only for single
  segments keeps the rest and reverts those segments (`reverted_segments`).
- **Keys:** never printed, logged or committed; importing a module reads
  neither `.env` nor a secret. A new pipeline key goes into
  `config.SECRET_ENV_VARS`, an evaluation key into `secret_registry`, both
  into `.env.example`.
- **Tests offline:** pytest-socket blocks the network; use the fakes.
- **No names:** no speaker or interviewee names, no accent or nationality
  labels (only "native / non-native speaker"), no transcripts or long quotes
  in tracked files; `scripts/check_names.py` must report 0 hits. Test-set
  sources (with speaker names) are named only in the Sources section of
  `data/test-set/README.md`, the name check's one allow-listed section. No personal email, no employer names.
- **Data:** media, references and run outputs are never committed; check
  `git status` before a commit. Only text goes to the Claude API.
- **Measurement:** every run gets a row in `results/benchmark.csv` and an
  entry in `docs/experiments.md`; no number in the docs that was not
  measured; estimate the cost before a full-video run (above $3 needs the owner).
- **Code:** English everywhere; structured outputs; stages keep segment ids
  (translation works on sentence units with `source_ids`); core logic free of
  CLI I/O; `target_lang` is a parameter.
- **Commits:** `Area: what changed` (at most 72 characters; Docs, CI, STT,
  Correction, Translation, Subtitles, Metrics, Eval, CLI, Tests, Config,
  Tooling), the body says why. A `Co-Authored-By: Claude <model>` trailer is
  allowed; never a `Claude-Session:` trailer or a claude.ai link. Run
  `scripts/check.ps1` before every push.

## State

Version 1.0.0 tagged `v1.0` (2026-11-12); package metadata now says 1.1.0
(`CHANGELOG.md`, date set with the `v1.1` tag); the repository stays private.
Gate 2 -> 3 passed on 2026-11-11 (T6 run once: pass, not demonstrated).
Independent LLM reviews #1 and #2 done and answered (`docs/review-*.md`). The
web UI is built (`docs/web.md`); left for v1.1: the owner tries the UI, the
git-history rewrite (dry run: `work/rewrite-dryrun-report.md`), publication
and the `v1.1` tag (`docs/experiments.md`, Backlog › v1.1; never rewrite
history or force-push before then). VideoLingo is out of scope; dubbing is stage 4.

## Pitfalls

- faster-whisper 1.2.1 cannot decode files with PyAV 19; the engine reads the WAV itself.
- Gemini `gemini-3.8-flash` often answers 503; the free tier allows 20 requests a day per
  model. Gemini timestamps are model estimates: never time subtitles with them unaligned.
- Use the uv-managed Python 3.13 (`uv run`), not the system `python`.
