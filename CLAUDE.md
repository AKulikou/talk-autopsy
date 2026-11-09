# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

**Talk Autopsy** turns an English technical talk, lecture or interview (a video
URL or a local file) into subtitles in a target language, later also a dubbed
audio track. It runs locally; only text goes to the Claude API.

```
input (URL/file) -> ffmpeg audio.wav + meta.json
  -> STT engine (pluggable; first: faster-whisper large-v3, CPU) -> raw.json
  -> Claude correction (terms, names, accent-distorted words; glossary + video context) -> fixed.json
  -> Claude translation (blocks of 5-10 min with overlap, one glossary per video) -> <lang>.json
  -> subtitles .srt (target language + corrected English) | later: TTS dub (edge-tts + ffmpeg)
```

The research question is measurement, not features: every stage is benchmarked on
a fixed test set (six 5-10 minute fragments, T6 sealed as a hold-out) against
baselines (raw Whisper, Whisper + Google Translate, VideoLingo). The target
language is a parameter (`target_lang`); only Russian is measured and accepted.

Part of the Autopsy series; repository conventions follow Startup Autopsy.

## Commands

```bash
uv sync                                    # Python 3.13 venv in .venv + dependencies from uv.lock
uv run pytest                              # unit tests: fake Claude client, generated audio, no network
uv run ruff check .                        # lint (config in pyproject.toml, target Python 3.13)
uv run talk-autopsy cut <source> --start 00:00 --end 10:00 --out <file>.wav   # 16 kHz mono WAV
uv run talk-autopsy transcribe <file>.wav --out work/T2/raw.whisper-large-v3.json   # faster-whisper, CPU
uv run talk-autopsy transcribe <file>.wav --engine assemblyai --out work/T2/raw.assemblyai.json  # cloud, uploads audio
uv run talk-autopsy draft <transcript>.json --out data/test-set/T2.draft.txt      # for proofreading
uv run talk-autopsy ref-from-draft data/test-set/T2.draft.txt --out data/test-set/T2.ref.txt
uv run talk-autopsy eval T2 --hyp <transcript>.json [--raw <raw>.json] [--record --engine <e> --stage <s>]
uv run talk-autopsy run <source> --lang ru --steps stt,fix,translate,srt      # prints the plan (stages not implemented)
```

Requires ffmpeg (with ffprobe) on PATH. `ANTHROPIC_API_KEY` in `.env` (never commit
it) is needed only by the Claude stages; the CLI checks it before any work on a run
that includes `fix` or `translate`. Run `uv run pytest` and `uv run ruff check .`
after any change. Use the uv-managed Python 3.13, not the system `python`.

## Layout

- `talk_autopsy/config.py` — API key check (`require_api_key`), optional cloud STT keys (`require_key`), `TARGET_LANG` and `check_lang`, model IDs, prices for the cost estimate, paths
- `talk_autopsy/segments.py` — `Transcript` / `Segment` / `Word`: the one JSON format every stage reads and writes; `assert_same_ids`
- `talk_autopsy/ingest.py` — `extract_audio` (ffmpeg, optional cut), `probe` (duration), `parse_timecode`; `download` (yt-dlp) is a stage 1 stub
- `talk_autopsy/glossary.py` — `Glossary`: a Markdown table per video (`T<n>.glossary.md`), format in the module docstring
- `talk_autopsy/stt/` — `base.py` `Transcriber` protocol and `drop_wordless`; `faster_whisper.py` first engine (large-v3, int8, CPU; reads the WAV itself, see its docstring); `assemblyai.py` cloud engine (Universal-3.5 Pro, keyterms with `--glossary-prompt`)
- `talk_autopsy/metrics.py` — pure metric functions: `tokenize` (the one normalization), WER, term accuracy, false-edit rate, reading speed; timing drift is an interface only
- `talk_autopsy/evaluation.py` — reads hypothesis/reference/glossary files, `evaluate`, the benchmark row and its append
- `talk_autopsy/proofread.py` — draft export (`[mm:ss] text`) and reference import for hand-checked transcripts
- `talk_autopsy/correct.py`, `translate.py` (stage 2), `subtitles.py` (stage 3), `tts/edge.py` (stage 4) — stubs with input -> output in the docstring
- `talk_autopsy/cli.py` — typer CLI (`cut`, `transcribe`, `draft`, `ref-from-draft`, `eval`, `run`); argument parsing and printing only
- `talk_autopsy/core/` — shared with autopsy-template: `structured_call.py` (structured request, stop-reason checks, validation, one retry), `claude_client.py`, `batch_client.py` (Message Batches at half price), `token_summary.py` (cost)
- `tests/` — `conftest.py` has the fake Claude client (`fake_claude`) and the generated sine WAV (`sine`)
- `data/test-set/` — media and reference transcripts are local only; `README.md` and `*.glossary.md` are tracked
- `docs/decisions.md` — decision log; `docs/experiments.md` — experiment log and Backlog
- `results/benchmark.csv` — one row per measured run (tracked); everything else in `results/`, `logs/`, `work/` is git-ignored

## Data rules

- Media (video, audio), reference transcripts (`T<n>.ref.txt`) and run outputs are
  never committed. Check `git status` before every commit.
- Only text goes to the Claude API: transcript segments, the glossary and a short
  video context. Audio leaves the machine only through a cloud STT engine
  (`--engine assemblyai` or `gemini`), which the owner approved for the test-set
  fragments; video never does. Engine keys (`ASSEMBLYAI_API_KEY`,
  `GEMINI_API_KEY`) are never printed or logged.
- `target_lang` is a parameter. No language is hard-coded in the core: stage
  functions take it as an argument; the default lives only in `config.py`.
- The test-set README names no video titles, speakers, channels or links.

## Engineering standards

- Production quality: explicit error handling, no silent fallbacks that fabricate
  data (a cut past the end of a file is an error, not a shorter file).
- Prefer structured outputs (`core/structured_call.py`) over parsing free text.
- Stages never renumber, merge or split segments; correction and translation
  return the same ids (`segments.assert_same_ids`).
- Keep core logic free of CLI I/O so a web UI can wrap it later.
- Stage modules get the client as `claude_client.get_client()` (through the
  module), so the test fixture can replace it.
- Code, comments, docstrings, docs and commit messages in English.
- Realistic estimates for time and cost; no number in the docs that was not measured.

## Commits

- Subject `Area: what changed`, at most 72 characters. Areas: Docs, CI, STT,
  Correction, Translation, Subtitles, TTS, Metrics, Eval, CLI, Tests, Config.
- The body says why.
- A `Co-Authored-By: Claude <model> <noreply@anthropic.com>` trailer is allowed.
  **Never add a `Claude-Session:` trailer or any claude.ai link.**
- No personal email anywhere (the repository identity is the GitHub noreply
  address); no employer names.

## Stage gates

| Stage | Content | Gate to the next stage |
|---|---|---|
| 0 | Foundation: repository, data format, audio cut, test set | — |
| 1 | STT selection | WER ≤ 15% on the accented fragment (T2) |
| 2 | Claude correction + translation | term accuracy ≥ 85%; blind translation score ≥ 4/5; hold-out T6 run once; external audit |
| 3 | CLI tool with subtitles | external audit before v1.0 |
| 4 | Dubbing | — |

Prompts and the engine freeze after a gate. New ideas go to the Backlog in
`docs/experiments.md`, not into the current stage. T6 is never listened to or used
for prompt work; it runs once, at the stage 2 gate, with frozen prompts.

## Limits

- At most 3 runs per hypothesis.
- Stage 1: at most 10 test-set runs and $5.
- Stage 2: at most 10 correction-prompt versions, 5 translation-prompt versions, $20.
- Estimate the cost before any full-video run; anything above $3
  (`COST_CONFIRMATION_USD`) needs the owner's explicit confirmation.
- Every run is recorded: a row in `results/benchmark.csv` and an entry in
  `docs/experiments.md` (hypothesis, what was run, numbers, conclusion, decision).

## Roadmap

- **Stage 0 (current):** foundation. Repository, CI, shared core, data format,
  `cut` command, test set T1–T2.
- **Stage 1:** prior-art survey; T3–T6 selected; reference transcripts; STT
  engine chosen against the WER gate; baselines measured.
- **Stage 2:** correction and translation prompts, measured and frozen; hold-out
  and external audit at the gate.
- **Stage 3:** the CLI tool with subtitles; external audit; v1.0.
- **Stage 4:** dubbed audio track.
- **v1.1:** local web UI over the same core.

## Known issues

- Nothing is measured yet: speech recognition and the metrics work, but no reference transcript exists until the owner proofreads the drafts. Correction, translation, subtitles and dubbing are stubs.
- faster-whisper 1.2.1 cannot decode files with PyAV 19 (`metadata_errors`); the engine reads the WAV itself and passes samples.
- faster-whisper's segment `avg_logprob` is per 30-second decoding window, so segment `confidence` is too; word probabilities are per word.
- T3–T6 are not selected; T1's speaker is assumed native (to be confirmed).
- Claude Haiku 5.5 costs more for prompts over 100,000 tokens; the cost estimate
  uses the lower tier.
- The shared core was written against `anthropic` 1.x; the lock has 2.x. Its
  unit tests pass with the fake client, but no real call has been made yet.
