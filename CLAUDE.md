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

- `talk_autopsy/` — `cli.py` (typer; parsing and printing only), `pipeline.py`,
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
  for every secret.
- `data/test-set/` — only `README.md` and `*.glossary.md` are tracked; media and
  reference transcripts stay local.
- `docs/experiments.md` (experiment log, metrics, criteria, Backlog),
  `docs/decisions.md`, `docs/review-*.md`, `docs/prior-art.md`;
  `results/benchmark.csv` (one row per measured run).
- `scripts/` — `check.ps1` / `check.sh`, `check_names.py` +
  `identifying_patterns.txt`, evaluation scripts, `windows/` (the owner's launcher).

## Rules

- **Frozen paths** (git tag `pipeline-v3`): `ingest.py`, `stt/`, `align.py`,
  `glossary.py`, `glossary_auto.py`, `correct.py`, `blocks.py`, `units.py`,
  `translate.py`, `segments.py`, `pipeline.py`, `config.py`, `core/`,
  `metrics.py`, `evaluation.py`, `ref_bounds.py`, the three prompts in use,
  `uv.lock`. A change needs a new tag and a new hold-out; check with
  `git diff --stat pipeline-v3 -- <paths>`. New ideas go to the Backlog.
- **Keys:** never printed, logged or committed; importing a module reads
  neither `.env` nor a secret. A new pipeline key goes into
  `config.SECRET_ENV_VARS`, an evaluation key into `secret_registry`, both
  into `.env.example`.
- **Tests offline:** pytest-socket blocks the network; use the fakes.
- **No names:** no speaker or interviewee names, no accent or nationality
  labels (only "native / non-native speaker"), no transcripts or long quotes
  in tracked files; `scripts/check_names.py` must report 0 hits. Test-set
  sources are named in `data/test-set/README.md` once the owner has checked
  them (decision 2026-11-12). No personal email, no employer names.
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

Stage 3 (v1.0). Gate 2 -> 3 passed on 2026-11-11 (T6 run once: pass, not
demonstrated). Independent LLM reviews #1 and #2 done and answered
(`docs/review-*.md`). Remaining for v1.0: git history rewritten without
identifying details, test-set sources published, tag `v1.0`, repository made
public. VideoLingo is out of v1.0 scope; the web UI is v1.1; dubbing is stage 4.

## Pitfalls

- faster-whisper 1.2.1 cannot decode files with PyAV 19; the engine reads the WAV itself.
- Gemini `gemini-3.8-flash` often answers 503; the free tier allows 20 requests a day per
  model. Gemini timestamps are model estimates: never time subtitles with them unaligned.
- Use the uv-managed Python 3.13 (`uv run`), not the system `python`.
