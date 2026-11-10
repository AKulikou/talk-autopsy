# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

**Talk Autopsy** turns an English technical talk, lecture or interview (a video
URL or a local file) into subtitles in a target language, later also a dubbed
audio track. It runs locally; only text goes to the Claude API.

```
input (URL/file) -> ffmpeg audio.wav (+ audio.flac for upload) + meta.json
  -> AssemblyAI, first pass -> raw.json          (fallbacks: chunked Gemini + forced alignment, then faster-whisper)
  -> auto-glossary (Haiku, from raw.json + context), merged with the user's glossary -> glossary.md
  -> AssemblyAI with the glossary as key terms -> raw2.json
  -> Claude correction (correction-v2; terms, names, accent-distorted words) -> fixed.json
  -> sentence units (segments merged to sentence ends, split above 25 s; units.py)
  -> Claude translation (translation-v2 on units; blocks of 5 min with context, no meaning compression) -> <lang>.json
  -> subtitles .srt + .ass (target language timed by the English words + corrected English) | later: TTS dub
```

The research question is measurement, not features: every stage is benchmarked on
a fixed test set (six 5-10 minute fragments, T6 sealed as a hold-out) against
baselines (raw Whisper, Whisper + Google Translate, VideoLingo). The target
language is a parameter (`target_lang`); only Russian is measured and accepted.

Part of the Autopsy series; repository conventions follow Startup Autopsy.

## Commands

```bash
uv sync                                    # Python 3.13 venv in .venv + dependencies from uv.lock
uv sync --extra align                      # + CPU torch/torchaudio for forced alignment (about 0.9 GB with the model)
uv run pytest                              # unit tests: fake Claude client, generated audio, no network
uv run ruff check .                        # lint (config in pyproject.toml, target Python 3.13)
pwsh scripts/check.ps1                     # before every push: exactly what CI runs, secrets cleared (scripts/check.sh elsewhere)
uv run talk-autopsy cut <source> --start 00:00 --end 10:00 --out <file>.wav   # 16 kHz mono WAV
uv run talk-autopsy transcribe <file>.wav --out work/T2/raw.whisper-large-v3.json   # faster-whisper, CPU
uv run talk-autopsy transcribe <file>.wav --engine assemblyai --out work/T2/raw.assemblyai.json  # cloud, uploads audio
uv run talk-autopsy transcribe <file>.wav --engine gemini --out work/T2/raw.gemini.json          # cloud, uploads audio
uv run talk-autopsy transcribe <file>.wav --engine gemini+align --out work/<run>/raw.gemini+align.json  # chunked Gemini + forced alignment
uv run talk-autopsy transcribe <file>.wav --threads 8 ...                                       # faster-whisper CPU threads
uv run talk-autopsy align <transcript>.json <file>.wav --out <aligned>.json                     # measured word times
uv run talk-autopsy srt <transcript>.json --out <file>.srt [--glossary <md>]                    # subtitles: .srt + sibling .ass
uv run talk-autopsy glossary-auto <raw>.json --out <glossary>.md [--context <text-or-file>] [--compare <ref>.md]  # Haiku auto-glossary (B5)
uv run talk-autopsy fix <transcript>.json --glossary <md> --out <fixed>.json [--model <id>] [--prompt correction-v1]  # Claude correction (default v2), prints the estimate
uv run talk-autopsy translate <fixed>.json --lang ru --glossary <md> --out <ru>.json [--prompt translation-v1]       # Claude translation (default v2)
uv run talk-autopsy adjudicate T2 --backbone work/T2/raw.whisper-large-v3.json --hyp work/T2/raw.assemblyai.json --hyp work/T2/raw.gemini.json
uv run talk-autopsy resolve T2                    # after ticking data/test-set/T2.adjudication.md -> T2.ref.txt + protocol
uv run talk-autopsy judge T6 --resume             # blind audio judge (Gemini, free tier): work/T6/judge.jsonl + T6.adjudication.judged.md; counts only
uv run talk-autopsy resolve T6 --adjudication data/test-set/T6.adjudication.judged.md   # reference from the judge's decisions
uv run talk-autopsy judge-compare T2              # judge vs the decided T2 spans (prints span texts: never on a sealed fragment)
uv run talk-autopsy eval T2 --hyp <transcript>.json [--raw <raw>.json] [--record --engine <e> --stage <s>]
uv run talk-autopsy run <source> --lang ru [--context <text-or-file>] [--glossary <md>]
                                           # the pipeline (default steps stt,glossary,stt2,align,fix,translate,srt; engine assemblyai);
                                           # work/<video-id>/, skips steps that are up to date; --dry-run prints the plan;
                                           # --engine gemini+align|faster-whisper|gemini; --gemini-cache-only: no Gemini call
```

`fix`, `translate` and `eval` are the experiment commands: no refusal fallback, one
named model per benchmark row. `run` is the tool: refusal fallback on.

Requires ffmpeg (with ffprobe) on PATH. `ANTHROPIC_API_KEY` in `.env` (never commit
it) is needed only by the Claude stages; the CLI checks it before any work on a run
that includes `glossary`, `fix` or `translate`. `ASSEMBLYAI_API_KEY` is needed by the
default `run`; `GEMINI_API_KEY` only for the Gemini engine or fallback. Run `uv run pytest` and `uv run ruff check .`
after any change, and `scripts/check.ps1` before every push (CI also scans the full git history
for secrets with gitleaks). Use the uv-managed Python 3.13, not the system `python`.

## Layout

- `talk_autopsy/config.py` — `load_environment` (reads `.env`; called only by entry points: the CLI's `main`, later the web UI), API key check (`require_api_key`), optional cloud STT keys (`require_key`), settings as functions read on call (`target_lang()`, `correction_model()`, ...), `check_lang`, model IDs, prices for the cost estimate, paths. Importing any module reads neither `.env` nor a secret
- `talk_autopsy/segments.py` — `Transcript` / `Segment` / `Word`: the one JSON format every stage reads and writes; `assert_same_ids`; `Segment.source_ids` only in sentence units
- `talk_autopsy/units.py` — sentence units (`sentence-units-v1`): segments merged until terminal punctuation or a pause over 1.5 s, split above 25 s at the best boundary near the even split; each unit keeps its English words and the covered segment ids
- `talk_autopsy/ingest.py` — `extract_audio` (ffmpeg, optional cut), `probe` (duration), `parse_timecode`; `download` (yt-dlp) is a stage 1 stub
- `talk_autopsy/glossary.py` — `Glossary`: a Markdown table per video (`T<n>.glossary.md`), format in the module docstring
- `talk_autopsy/stt/` — `base.py` `Transcriber` protocol and `drop_wordless`; `faster_whisper.py` first engine (large-v3, int8, CPU, `cpu_threads`; reads the WAV itself, see its docstring); `assemblyai.py` cloud engine (Universal-3.5 Pro, keyterms with `--glossary-prompt`); `gemini.py` cloud engine (Files API upload, verbatim JSON transcript; timestamps are model estimates, never for subtitles; typed errors `GeminiUnavailable` / `GeminiQuotaExhausted`); `chunked.py` long audio through Gemini (chunks cut at silences, per-chunk cache, model fallback list, faster-whisper for a failed chunk, quota error stops, `ProgressEvent`s)
- `talk_autopsy/align.py` — forced alignment (wav2vec2 base 960h from torchaudio, Viterbi in numpy, block by block with garbage states); `AlignedTranscriber` = the `gemini+align` engine; `start_differences` for timing comparisons. torch is the optional `align` extra; tests use a stub emitter
- `talk_autopsy/metrics.py` — pure metric functions: `tokenize` (the one normalization; drops fillers uh/um/er/ah/hmm), WER, term accuracy (base form or regular plural of the last word), false-edit rate, reading speed; timing drift is an interface only
- `talk_autopsy/evaluation.py` — reads hypothesis/reference/glossary files, `evaluate`, the benchmark row and its append
- `talk_autopsy/adjudicate.py` — reference by consensus: aligns two cloud transcripts to the faster-whisper backbone, spans of disagreement, the adjudication file, clips, `resolve` to the reference and its protocol (`other: (no words)` types an empty variant)
- `talk_autopsy/judge.py` — blind audio judge (`audio-judge-v1`, evaluation tooling, not frozen): one Gemini request per majority/split span with the clip inline; `heard` (verbatim, written first) then `choice` among deduplicated, shuffled, unlabeled options or `none` + `final_text`; resumable `judge.jsonl`; filled copy `T<n>.adjudication.judged.md`; self-preference / none / consistency shares; `compare` and `classify_decision` for calibration on decided spans
- `talk_autopsy/ref_glossary.py` — reference glossary drafted by Gemini (`ref-glossary-v1`, not Claude, so the scored corrector does not write its own glossary): terms from `T<n>.ref.txt`, kept only if `eval` finds them in the reference, at most 60 (`talk-autopsy glossary-ref T<n>`)
- `talk_autopsy/proofread.py` — draft export (`[mm:ss] text`) and reference import; superseded by `adjudicate.py` for the test set (`draft`, `ref-from-draft` still work)
- `talk_autopsy/blocks.py` — what correction and translation share: 5-minute blocks with 3 read-only context segments, video context from `meta.json`, token and cost estimates, stage meta
- `talk_autopsy/glossary_auto.py` — auto-glossary (`glossary-auto-v1`, helper model): terms, `keep`, suggested translation, `heard_as` mis-recognitions, evidence rule; `coverage` against a reference glossary; `merge` with a user glossary (user wins, drafted translations become "suggested" notes)
- `talk_autopsy/correct.py` — Claude correction (`correction-v2` default, `correction-v1`), validator (incl. 70% words kept in order, no runs of words from a neighbouring segment), failed blocks keep their text and are listed; `translate.py` — Claude translation (`translation-v2` default: no meaning compression; `translation-v1`) of sentence units by default (`units=False` / `--per-segment`: each segment alone), `target_lang` a parameter; prompts in `talk_autopsy/prompts/<version>.md`
- `talk_autopsy/subtitles.py` — SRT and `.ass` writer: up to 3 x 48 characters, at most 7 s, breaks at sentence/clause ends, never inside a glossary term; a translated unit's cues timed by the English words they cover; every cue extended into the following pause (up to +1.5 s, 0.1 s before the next cue, at most 7 s); `.ass` 1920x1080, Arial 42, outline, bottom centre; reading speed reported (shares above 17 and 25 cps), never enforced
- `talk_autopsy/pipeline.py` — `run`: stt (AssemblyAI on `audio.flac`; fallback chunked Gemini, then faster-whisper), glossary, stt2 (key terms), align (model-estimated times only), fix, translate, srt in `work/<video-id>/`; stamp files to skip unchanged steps, key checks first, cost estimate and limit, Claude and speech recognition cost report, fallbacks listed, progress events
- `talk_autopsy/core/structured_call.py` also has `refusal_fallback` (`fallbacks: "default"` through the beta endpoint); `run` turns it on for Sonnet calls
- `talk_autopsy/tts/edge.py` (stage 4) — stub with input -> output in the docstring
- `talk_autopsy/cli.py` — typer CLI (`cut`, `transcribe`, `align`, `glossary-auto`, `fix`, `translate`, `srt`, `draft`, `ref-from-draft`, `adjudicate`, `resolve`, `eval`, `run`); argument parsing and printing only
- `talk_autopsy/core/` — shared with autopsy-template: `structured_call.py` (structured request, stop-reason checks, validation, one retry), `claude_client.py`, `batch_client.py` (Message Batches at half price), `token_summary.py` (cost)
- `tests/` — `conftest.py` has the fake Claude client (`fake_claude`), the generated sine WAV (`sine`) and a placeholder for every secret in `config.SECRET_ENV_VARS` (autouse); `test_secrets.py` fails on an environment read of a `*_KEY`/`*_TOKEN`/`*_SECRET` name missing from the registry, and on a `.env.example` out of sync with it. A new provider key: add it to `SECRET_ENV_VARS` and `.env.example`, nothing else
- `data/test-set/` — media and reference transcripts are local only; `README.md` and `*.glossary.md` are tracked
- `docs/decisions.md` — decision log; `docs/experiments.md` — experiment log and Backlog
- `results/benchmark.csv` — one row per measured run (tracked); everything else in `results/`, `logs/`, `work/` is git-ignored
- `scripts/check.ps1`, `scripts/check.sh` — the CI test job, run locally with the secrets cleared

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
- Stages never renumber, merge or split segments; correction returns the same
  ids (`segments.assert_same_ids`). The one exception is translation, which
  works on sentence units (`units.py`): new ids, each unit listing the segment
  ids it covers (`source_ids`) and keeping the English word timings.
- Keep core logic free of CLI I/O so a web UI can wrap it later.
- Stage modules get the client as `claude_client.get_client()` (through the
  module), so the test fixture can replace it.
- Code, comments, docstrings, docs and commit messages in English.
- Realistic estimates for time and cost; no number in the docs that was not measured.
- Tests never touch the network or a real secret: pytest-socket (`--disable-socket`, in
  `pyproject.toml`) makes opening an internet socket or a DNS lookup raise, so a test
  that would reach a service fails instead; use the fakes in `tests/conftest.py`. Every
  secret in `config.SECRET_ENV_VARS` is a placeholder inside a test, and importing a module
  reads neither `.env` nor a secret (only entry points call `config.load_environment()`).

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

- **Stage 0 (done):** foundation. Repository, CI, shared core, data format,
  `cut` command, test set T1–T2.
- **Stage 1 (gate passed 2026-11-09):** reference transcripts for T1–T2; STT
  engine chosen against the WER gate: AssemblyAI with key terms (owner's choice,
  decisions), Gemini + forced alignment and faster-whisper as fallbacks;
  baselines measured. Still open from the plan: prior-art survey, T3–T5.
- **Stage 2 (current):** correction and translation prompts, measured and frozen; hold-out
  and external audit at the gate. C1 met (blind pairwise: v1 4.40, v2 4.67); B2 v2 checked.
  Frozen 2026-11-10 as git tag `pipeline-v2` (`docs/experiments.md`, Freeze pipeline-v2):
  AssemblyAI two passes, glossary-auto-v1, correction-v2, sentence units, translation-v2;
  a change to a frozen part needs a new tag and a new hold-out. T6: three engines run and
  `data/test-set/T6.adjudication.md` prepared; the owner adjudicates every span, then a later
  session resolves the reference and runs the tagged pipeline on T6 once; then the external audit.
- **Stage 3:** the CLI tool with subtitles; external audit; v1.0.
- **Stage 4:** dubbed audio track.
- **v1.1:** local web UI over the same core.

## Known issues

- Gemini's free tier allows 20 requests per day per project for `gemini-3.8-flash` (429 `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, measured 2026-11-10; not in the docs). The blind audio judge (one request per span) therefore judges at most about 20 spans a day; rerun `judge <ID> --resume` after the reset (midnight Pacific).
- T1's reference was decided by a Claude model without listening; it is secondary evidence for the Claude stages.
- Gemini's free tier: 503 "high demand" is frequent and the daily quota can run out within one long talk (2026-11-09: on the third chunk). The chunked engine falls back per chunk on 503 and stops on quota; `run --gemini-cache-only` finishes a video with faster-whisper for the missing chunks.
- Forced alignment places word starts 130–230 ms later than faster-whisper does; which is closer to the truth is not measured (no hand-labelled onsets yet).
- Glossaries are reviewed against the references, so term accuracy with a glossary in the prompt is optimistic for unseen videos. Dubbing is a stub.
- Gemini `gemini-3.8-flash` often answers 503 UNAVAILABLE ("high demand"; 8 of 10 calls on 2026-11-09). The engine retries after 20, 60 and 120 s and then fails; rerun later. Its timestamps are model estimates and may run past the end of the audio.
- AssemblyAI and Gemini helped build the T1/T2 references, so their WER against them is biased in their favour; compare them through the resolve protocol (docs/experiments.md, methodology).
- faster-whisper 1.2.1 cannot decode files with PyAV 19 (`metadata_errors`); the engine reads the WAV itself and passes samples.
- faster-whisper's segment `avg_logprob` is per 30-second decoding window, so segment `confidence` is too; word probabilities are per word.
- T3–T5 are not selected; T6 is cut blind (25:46–35:46 of `T6.source.mp4`). Since the `pipeline-v2` tag its engine transcripts exist (`work/T6/`), but nothing derived from its content goes into chat, commits, `docs/`, printed logs or any file outside `data/test-set/` and `work/T6/`: only counts, durations and costs. The assistant never reads its transcripts and decides none of its spans. T1's speaker is assumed native (to be confirmed).
- AssemblyAI costs about $0.47 per hour of video in `run` (two passes, list price: $0.21 + $0.26 with key terms), drawn from the account's free credit for now.
- Claude Haiku 5.5 costs more for prompts over 100,000 tokens; the cost estimate
  uses the lower tier.
- The shared core was written against `anthropic` 1.x; the lock has 2.x (2.6.0).
  Real calls work since 2026-11-09 (B1, B4): `messages.parse` with
  `output_format`, prompt caching (the second run within five minutes reads the
  prefix). Server-side refusal fallback is deliberately off (decisions).
