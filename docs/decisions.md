# Decision log

Decisions that shape the project: pipeline, scope, test set, process. Newest
first. Experiments and their numbers go to [experiments.md](experiments.md); this
log records what was decided and why.

Entry format: date, decision, why, and what it rules out.

## 2026-11-14 — Agreed terms accept Russian case endings of short words; `pipeline-v3.7`

Decision (v1.2 session C2, Part 1): the agreed-translation check rejecting
`цену` for the agreed `цена` (found in C1, Backlog) is a **bug**: the check
exists to catch a *different* rendering, and an inflected form of the agreed
word is the agreed rendering. Fixed under the bug rules.

The bug: `translate._stem_present` kept at least four letters of each word of
an agreed translation as a prefix, so a 4-letter word had to appear whole; its
accusative, instrumental or plural failed. On T7 one real sentence per (b) run
(`gas price`, agreed `цена газа`) stayed English after both answers were refused
(logs of the three runs: 12 refusals, the only ones in every saved log and
output). Which form the refused answers used is not known (refused answers are
not stored), so the failing test is synthetic.

**The rule** (docstring of `_stem_present`): a word of the agreed translation
is present if a word of the answer starts with its first `len - 2` letters (at
least 4), as before, **or**, for a Russian word, if cutting one case or number
ending of nouns and adjectives (a fixed list, longest first) from each leaves
the same stem of at least 3 letters (`цена` / `цену` / `ценой` / `цены` /
`цене`, stem `цен`; `газа` / `газ`). A different word with the same start
(`ценность`, `цент`) or a synonym (`стоимость`) still fails (tests). The rule
only adds accepted forms; nothing that passed before fails now.

Proofs:

| | result |
|---|---|
| (a) failing test committed first | `eff748b` (5 failures before the fix, the 3 rejection cases pass before and after) |
| (b) golden requests | `pipeline_requests` and `direct_requests` byte-identical; `pipeline_outputs` differs only in `ru.json.stamp.json` (the translate step's code key) |
| (c) saved answers, offline | 22 work folders (T1, T2, T6, T7, real runs), 82 blocks: 82 the same verdict with the old and the new check |
| (d) T2 from cache | after a one-off carry-over of 19 translation stamps (`e5c5568f` -> `80969d8b`, backups kept locally): every model step skipped, 0 USD, the 22 files of `work/T2-v3-cache/` byte-identical before and after the run; the stamp differs from its backup only in `code`. The three T7 (b) runs keep their old stamps: block 3 might have been accepted at the first answer under the new rule, so their stored results are not what the new code would make |
| requests | not changed: the check decides whether a second attempt is sent, never what a request contains |

Two Backlog items fixed in passing as bugs (not in frozen paths, no tag
needed for them): a local file's result folder and file names were still cut
in the middle of a word (only YouTube names were fixed in v1.1; now
`youtube.local_result_name`, an existing folder under the old name is reused);
the time estimate's "not counting: <Step>" put a capitalised step name in
mid-sentence (now in lower case, an acronym keeps its capitals;
`i18n.excludedSteps`). Failing tests `9caaec1`.

## 2026-11-14 — Commands and spoken names are not English left untranslated; `pipeline-v3.6`

Decision (owner, after the T7 baseline): the T7 rejections of `make anvil.`
and similar segments are a **bug** of the `pipeline-v3.3` class (the check
"left in English" counts code as untranslated words), not an improvement as
first classified (entry below). Fixed under the bug rules.

**Measured first, from the saved T7 runs** (logs and `meta.translation`,
no new request):

| | (b) run 1 | (b) run 2 | (b) run 3 | (a) |
|---|---|---|---|---|
| segments flagged "left in English" (any attempt) | 7 | 3 | 5 | 6 |
| of them code (a command line, a path, a name read off the screen) | 6 | 1 | 5 | 5 |
| blocks sent twice (of 4); requests | 2; 6 | 2; 6 | 2; 6 | 2; 6 |
| segments in `reverted_segments` | 8 | 3 | 6 | 4 |
| of them: code / a printed string / a real sentence | 6 / 1 / 1 | 1 / 1 / 1 | 5 / 0 / 1 | 3 / 1 / 0 |

The real sentence in each (b) run stayed English because of a *different*
check, the agreed translation of `gas price` from the Web3 dictionary, not this
one; the printed string is a short phrase the tutorial's code outputs (left
flagged: it is English text); one first answer was also refused for a terminal
message (left flagged). No segment with real content was kept in English by the
"left in English" check except that printed string.

**The rule** (`translate.left_in_english`, written down in its docstring):
not counted as English words are, in addition to the verbatim tokens of
`pipeline-v3.3`, (1) a path without an extension (`script/deploy`), (2) a
lower-case hyphenated name or flag (`deploy-vault`, `--rpc-url`); (3) the parts
of kept identifiers count as kept words (`VaultNFT` -> vault, nft); a segment is
not left in English if it is (4) a command line: at most 6 tokens, starting with
a general shell or build program (`make`, `git`, `npm`, ... a fixed list; no
domain tool) or a kept one-word lower-case tool of the run glossary (`forge`),
followed by at least one code token; or (5) a name read off the screen: 2-4
capitalised source words, at least one a part of a kept identifier or acronym,
at most one outside the kept words, and the same letters and digits in the
target. Still caught (tests): an English sentence that starts with a program's
name, a kept tool's name with plain words, a terminal message, a short
lower-case phrase, a greeting with a person's name even when the name is a kept
term (found by the offline control on an old T2 experiment's glossary; the first
version of rule 5 let it through and was narrowed before the commit).

Proofs:

| | result |
|---|---|
| (a) failing test committed first | `9c9cfd1` (8 failures before the fix) |
| (b) golden requests | `pipeline_requests` and `direct_requests` byte-identical; `pipeline_outputs` differs only in `ru.json.stamp.json` (the translate step's code key); with the old key substituted the old golden files pass |
| (c) saved answers, offline | 22 work folders, 82 blocks: 78 the same verdict, 4 different = block 1 of the four T7 runs (now accepted); every T1, T2, T6 and real-run answer the same. Control (each unit's English as its own answer): 4622 of 4638 flagged before, 4605 after; the 17 no longer flagged are all T7 command lines or names |
| (d) T2 from cache | after a one-off carry-over of 19 translation stamps (`269ac790` -> `e5c5568f`, backups kept): every model step skipped, 0 USD, the 22 files of `work/T2-v3-cache/` byte-identical before and after the run; the stamp differs from its backup only in `code` |
| requests | not changed: the check decides whether a second attempt is sent, never what any request contains |

Expected effect on the stored T7 answers (offline): block 1 is accepted at the
first answer in (b) runs 1 and 3 and in (a); (b) run 2's first answer of block 1
also had the terminal message, so it would still be sent twice; block 3 is
still sent twice where the printed string or `gas price` is refused. So 3 of the
8 repeated requests of the four runs would not be sent.

Found, not fixed (not this class): the agreed-translation check accepts
inflected forms by a stem of `len - 2` letters but at least 4, so a 4-letter
word of an agreed translation (`цена`) must appear whole and its accusative
(`цену`) fails. Whether the model wrote that form on T7 is not known (rejected
answers are not stored). Backlog.

## 2026-11-14 — T7 (v1.2): how its reference, glossary and evaluation are made; two run findings

Decisions made while adding T7, the Web3 tutorial fragment of v1.2
(`docs/experiments.md`, T7 baseline (v1.2)):

- **The window was fetched as a section, not the whole course** (owner):
  `yt-dlp --download-sections` on the HLS audio stream (a first try on the
  direct m4a stream stalled), 15 s of margin, then cut exactly. Speaker
  "not determined": nobody has listened to it, and nothing is guessed.
- **`ref-glossary-v2`** for T7 (evaluation tooling): the session asked for at
  least 60 terms, each in one of six categories; `ref-glossary-v1` keeps at
  most 60 after dropping absent terms and writes a free category. v2 has the
  same instructions plus 60-90 terms, version numbers and no names of people
  (they may not appear in tracked files). v1 is unchanged (its prompt hash is
  tested), so T6's glossary stays reproducible. Written before T7's reference
  existed; the Web3 dictionary was not opened.
- **`eval --bounds`** scores a fragment that is not sealed against both judge
  references with the bounds, as for T6; `config.SEALED_FRAGMENTS` (a frozen
  path) is not touched.
- **The blind evaluation shows one system** (owner, before the
  pre-registration was committed): only the pipeline's translation of 20
  replicas, a score and "scored lower because of a term". The E1 rule needs
  the absolute score and the term count, not a comparison; it halves the
  owner's work. It is not blind to the source.
- **Bug: `run <audio file> --output all` failed after the paid steps** (an
  audio file has no picture for `mp4-soft`, `mp4-burned`). Fixed in the CLI
  only (`cli/runs.py`, the web page's own rule), failing test first; no
  frozen path, no request or cache key changed, so no new pipeline tag.
- **(Superseded the same day: reclassified as a bug by the owner and fixed in `pipeline-v3.6`, entry above.)** First classified as not a bug: commands left in English rejected by the translation check.
  On T7 the check "left in English" rejected segments that are shell commands
  or spoken identifiers (`make anvil.`, `forge script, script/deploy mood.`,
  `Deploy Mood NFT.`): both attempts of a block were refused for them, and
  they stayed English (which is what a viewer should see for a command). The
  check works as documented (plain English words left untranslated); telling
  a command from English words needs new knowledge (a list of command-line
  programs, or the code on screen), so it is an improvement, not a bug like
  `0.8.26` (digits counted as words) or `/mcp` (a term in another written
  form). Backlog, with its cost. Searched the real-run logs: no other case
  besides the already fixed `0.8.26`.

## 2026-11-13 — A failure on the service's side is retried for a minute; `pipeline-v3.5`

Decision (owner, after the fourth check of v1.1): failures on a service's
side that pass by themselves (unavailable, overloaded, rate limited, timeout,
dropped connection) are retried automatically with growing waits, about a
minute in all, before a run stops. Same proof conditions as `pipeline-v3.3`.

The bug: a 34-minute video stopped at 15:20 in the correction step, block 3
(two blocks had been answered), on HTTP 503 Service Unavailable from the
Claude API. The Anthropic SDK retried twice, after 0.4 and 0.9 s, and gave
up 5.4 s after the first 503; the page said "Unexpected error
(ServiceUnavailableError)". A manual restart at 15:22 went through. Retries
existed but were far too short for an outage of a minute or two.

Fix (`core/transient.py`, `core/structured_call.py`): a Claude request that
fails this way is sent again, unchanged, after 5, 15 and 30 s (four tries;
the SDK's own two quick retries inside each try stay). Anything else (a bad
request, a key error, a refusal, a rejected answer) is not retried. A failed
try returned no answer, so its usage is never counted and nothing is written
for it; a run that still stops writes no partial step result and keeps the
finished steps. `spend_observer` (observability, like `result_observer`)
reports the usage of every paid response, so the page can show what a run
spent before it stopped.

Not changed: AssemblyAI. Its failures never stop a run (the first pass falls
back to Gemini or faster-whisper, the key-term pass keeps the first pass,
both visibly), and `stt/assemblyai.py` is in the speech-recognition cache
key; a retry there is an improvement for the Backlog.

Proofs:

| | result |
|---|---|
| (a) | `Tests: a transient service failure must be retried, not stop the run` (10 of 20 failing on the old code; the other 10 pin what held already: no retry of our own errors, no partial result) |
| (b) | golden request snapshots unchanged; no module in `pipeline.STEP_CODE` changed, so no cache key moved |
| (c) | the validators, `pipeline.py` and the prompts are unchanged since `pipeline-v3.4` (`git diff` empty), so every cached T2/T6 answer gets the same verdict |
| (d) | no paid run; the T2 re-run from cache with invalid keys: every model step skipped, 0 USD, the 22 files of `work/T2-v3-cache/` byte-identical |

## 2026-11-13 — Estimates and outputs follow the cache; the agreed-term check stays

Three decisions after the owner's third check, none of them a change to a frozen module:

- The estimate counts only what will run (`rerun_plan.py`): the steps whose
  cache stamps already match, the correction and translation blocks that would
  be reused, and MP4s that would be reused are left out of cost and time. It
  reads the stamps with the pipeline's own functions, and a test pins the plan
  to a real pipeline run. Before the correction runs, which translation blocks
  its new text changes is unknown: then the translation counts in full and the
  amount is shown as "up to". Rules out an estimate that guesses below what
  can be spent.
- An MP4 output is reused when its source video, subtitle files, encoder
  settings and ffmpeg are unchanged and the file is the one written (stamp in
  the result's service folder); otherwise it is made again. Rules out keying
  on file times alone (a replaced subtitle file with an older time).
- The translation check "agreed glossary translation not used" is kept: on
  every cached translation and every log it never fired (7 units exposed, all
  with the agreed rendering in an inflected form), so there is no false
  positive to fix.

## 2026-11-13 — One bad segment no longer costs a block; `pipeline-v3.4` (per-segment revert)

Decision (owner, after the third real video): when a correction or translation
answer fails a check that belongs to one segment, only that segment is kept as
it was; the block's other corrections or translations are used. Structural
failures (other segment ids, an edit for a segment outside the block,
unparseable output) still fail the whole block. Same proof conditions as
`pipeline-v3.3` (entry below).

The bug: correction block 3 of a 25:56 talk (10:03-15:03, 59 segments) was
rejected twice for "glossary terms removed: MCP" and all of it kept the
recognized text. The speaker says "run MCP", meaning the `/mcp` command (heard
the same in the first recognition pass without a glossary and by a local
faster-whisper check of the audio), so the
likely answer, "run /mcp", was right and the check was wrong: the term
counter reads "/mcp" as another word. The rejected answers are not stored (only
their request ids are logged), so this is the most likely reading, not a proven
one. The owner's first video lost five minutes of translation to one segment the
same way (`pipeline-v3.3`). The v1.1 session-4 audit had listed the
whole-block fallback of correction but filed "one failed segment rather than
the block" as an improvement; it is a bug.

Fixes (`pipeline-v3.4`):

- The retry is unchanged (same requests, same two attempts). After both are
  rejected, `revert_segments` takes the rejected answers (now carried by
  `InvalidResultError.rejected`): when every problem belongs to single segments
  and they are not all of the block, those segments get their input back
  (correction: the recognized text, translation: the English source) and lose
  their edits; a correction answer must then pass every check, including the
  block's length. Of the two answers the one that reverts fewer segments is
  used (the second on a tie). Listed in `meta.<stage>.reverted_segments` with
  their problems; the page names their minutes, not the block's.
- Correction: a glossary term written as a command or code name (`/mcp`,
  `@mcp`, in backticks or not) is not a removal.
- A result with a reverted segment is not finished: its stamp is `incomplete`.
  The next run sends only blocks whose request would differ or that failed or
  had a segment reverted, for correction as well now; the translation also
  reuses every block whose request is unchanged after the correction changed
  other blocks (`pipeline.reusable_answers`: same stamp apart from the input
  hash, the earlier input known, the same user text).

Rules out: accepting a segment that failed its check; reverting segments of
a block whose answer is structurally wrong; asking the model again for one
segment (a new kind of request).

Proofs:

| | result |
|---|---|
| (a) | `Tests: one bad segment must not cost a whole block` (5 of 6 failing on the old code; the sixth pins the validation messages word for word and passed on both) |
| (b) | `pipeline_requests`, `direct_requests`, `direct_outputs` unchanged; `pipeline_outputs` differs in the hashes of `fixed.json.stamp.json` and `ru.json.stamp.json` (the code keys); with the old code hashes substituted the whole snapshot is identical |
| (c) | offline over every cached result in `work/` (T2 cache, repeats, experiments, T6, operational and web videos): 74 correction and 86 translation block answers, all with the same verdict and the same problem list from the old and the new validators; 214 controls (first segment emptied, glossary terms cut, the English source as a translation) identical too; two translation-v3 outputs skipped (input stored elsewhere) |
| (d) | no paid test-set run; the T2 re-run from cache with invalid keys: every model step skipped, 0 USD, the 22 files of `work/T2-v3-cache/` byte-identical |

Cache keys: `code_hash("fix", "correction-v2")` `d935215dabcd307f` ->
`41fbd4778c04a067`, `code_hash("translate", "translation-v2.1")`
`9455a8051df5d39c` -> `269ac7905a3cdd7c` (every prompt version moves). A one-off
local script moved the 24 stamps holding the `pipeline-v3.3` hash to the new one,
keeping `incomplete` markers (10 older experiment stamps were already stale);
the old stamps were backed up. No prompt file changed.

## 2026-11-13 — The waiting screen's text panel is collapsed by default

Decision (owner, after the second check of v1.1): the two-column text panel is
not needed in everyday use, so the running screen opens with it collapsed; the
summary line and the warnings stay, and "Show text as it is processed" opens
it (remembered per browser). The README pictures still show it open: it is the
clearest picture of what the pipeline does. Rules out building rows nobody
sees: while collapsed the page fetches the summary only.

## 2026-11-12 — Bugs in frozen modules are fixed; `pipeline-v3.3` (untranslatable segments, failed blocks)

Decision (owner, v1.1 session 4): the freeze holds for improvements, not for
bugs, and every bug found before publication is fixed before publication. A
bug fix in a frozen module needs: (a) a failing test that reproduces it,
committed first; (b) the golden request snapshots unchanged (prompts and
everything sent to the models byte-identical); (c) every cached model answer of
T2 and T6 accepted or rejected by the new code exactly as by the old one,
checked offline; (d) no paid re-run of the test set; (e) a new tag. A fix that
cannot be made without changing what is sent to the models is not made.

The bug (the owner's first real video, a 13:40 programming tutorial):
segment 29 of translation block 1 was a bare version number, `0.8.26.`. The
"left in English" check counted digits as English words, so a correct
translation (the same digits) was rejected twice, and the whole first block,
about five minutes, stayed in English; both rejected attempts were billed. A
second bug made it permanent: the translation was stamped as up to date with
the failed block in it, so a re-run kept the English block.

Fixes (`pipeline-v3.3`):

- `translate.left_in_english` counts only plain words: tokens with a letter,
  outside tokens that stay as they are in a correct translation (URLs and web
  addresses, paths and file names, member access, snake_case, camelCase,
  letters joined with digits, calls). A segment with no plain word has
  nothing to leave in English. An ordinary English sentence is rejected as
  before, with or without numbers and identifiers in it (tests).
- A correction or translation with failed blocks gets a stamp that names them
  (`incomplete`), so it is never up to date. The next run of the translation
  sends only the failed blocks, with exactly the request of the first time
  (same prefix, context and segments; tested), and takes the accepted blocks
  from the earlier result (`translate(done=...)`, `meta.translation.reused_blocks`).
  A correction with a failed block runs again in full (cheap; changing
  `correct.py` for per-block reuse was not worth a second cache-key move).

Proofs:

| | result |
|---|---|
| (a) | `Tests: reproduce untranslatable-segment validation failure` (21 failing tests on the old code; the "English is still rejected" tests passed on it and still pass) |
| (b) | `pipeline_requests`, `direct_requests`, `direct_outputs` snapshots unchanged; `pipeline_outputs` differs in one value, the hash of `ru.json.stamp.json`, because the stamp holds the translate step's code hash: with the old hash substituted, the whole snapshot (requests, every file, every event) is identical |
| (c) | offline over every cached translation in `work/` (T2 cache, T2 repeats, T2 experiments, T6, three operational and three web videos): 74 accepted block answers, 74 identical verdicts and problem lists (two translation-v3 experiment outputs skipped: their input is not stored next to them). Control, the English source as the answer: the old and the new check reject the same 3,334 segments; on the failed block of the owner's video the new check stops rejecting exactly one segment, `0.8.26.` |
| (d) | no paid run; the T2 re-run from cache: every step skipped, 0 USD, the 22 files of `work/T2-v3-cache/` unchanged by the run |

Cache keys: `code_hash("translate", ...)` changes for every prompt version
(translation-v2.1: `6d5c408c0c2bc825` -> `9455a8051df5d39c`); the correction,
glossary and speech keys do not. A one-off local script carried the cached
translations over: each translation stamp whose code was the `pipeline-v3.2`
hash of its prompt got the new hash (9 stamps; 6 older experiment stamps were
already stale and were left alone), and the owner's video, whose result has a
failed block, also got `incomplete`, so its re-run sends only block 1. The old
stamps were backed up first. No prompt file changed.

Why: the check exists to catch a model that answers in English; a segment
with nothing to translate is not that. A block that fell back to English is a
failure, and caching it as a result hides it for good.

Rules out: loosening the check for ordinary sentences (a short English
sentence such as "Okay." is still rejected; product names outside the
glossary still count as English words); keeping the rest of a block when one
segment fails (Backlog).

## 2026-11-12 — One per-video cost limit for the CLI and the web page

Decision: `run --max-cost` (default 3 USD per video) is compared with the
whole estimate, Claude plus speech recognition, as the web page's cost gate
already was; one function, `youtube.over_video_limit`, serves the CLI (local
file, link, playlist plan) and the page. Before, the CLI compared the Claude
part only. A local file is checked right after its estimate is printed, a link
before the download; an unknown estimate is left to each caller (the page asks
for a confirmation, the CLI relies on the pipeline's own Claude estimate).

Why: one tool, one limit. The same video passed in the console and needed a
confirmation in the page for videos of about 2.3 to 3.7 hours, and the limit
is meant to bound what a video costs, not only its Claude part.

What it rules out: a second, Claude-only per-video limit. The pipeline's own
check (`max_cost_usd` against its Claude estimate after speech recognition) is
frozen code and stays as a later, narrower guard.

## 2026-11-12 — Web UI scope (v1.1)

Decision: the web page is a local, single-user front end to the same core,
with these limits on purpose:

- **Local only:** bound to 127.0.0.1, no option to listen elsewhere; every
  request is checked for Host, Origin and Sec-Fetch-Site, every POST carries a
  per-launch token; no CORS (`docs/web.md`, Security model).
- **One job, one video:** one job at a time in one worker thread; playlists
  stay with the CLI and the console launcher.
- **No stop button:** the core has no cooperative cancellation, and adding it
  would reopen frozen modules; closing the server stops a run, and a re-run
  continues from the finished steps.
- **Real progress only:** a bar only where the core reports a position
  (download bytes, correction and translation blocks, burn-in seconds);
  spinners and elapsed time elsewhere. Text appears block by block through the
  `pipeline-v3.1` observability exception (entry below).
- **Cost first:** cost and time estimate before anything is spent; above the
  per-video limit the request must carry that exact amount.
- **Interface languages** English, Russian and Lithuanian; the Lithuanian
  strings wait for a native speaker's check (Known limitations).
- **Record and replay:** every job is recorded locally (git-ignored); a hidden
  `--replay` mode plays a recording with no pipeline, network or cost, used for
  tests, demos and the README screenshots (committed recordings are of an
  invented talk).
- **No build step, no CDN:** plain HTML, CSS and JavaScript modules served by
  the app.

Why: the owner runs one talk at a time from a laptop; a local page replaces
the console prompts without a second implementation of the pipeline, and
without exposing a server that can spend money.

What it rules out (for v1.1): a hosted or multi-user service, playlists in the
page, cancelling a run, progress percentages for steps that report none.

## 2026-11-12 — Version 1.1.0 under the cache-key rule

Decision: the package version moves 1.0.0 → 1.1.0 in `pyproject.toml` and
`uv.lock`, treated like the 1.0.0 bump (a frozen path touched by package
metadata only) and checked with the cache-key rule below: the `uv.lock` diff is
the project's own `version` line (`uv lock`: "Updated talk-autopsy v1.0.0 ->
v1.1.0"); nothing in the package reads its own version; golden request tests
pass unchanged; T2 from cache skips every model step at 0 USD and all 22 files
are byte-identical. No new pipeline tag: `pipeline-v3.2` still describes the
pipeline.

Why: the release number should say what changed for the user (a web
interface), without reopening the measured pipeline.

## 2026-11-12 — Cache-key rule for frozen modules; `pipeline-v3.2` removes dead code

Decision (owner, v1.1 session 3): a change to a frozen path is allowed without
a new hold-out only if, afterwards, (a) the golden request tests
(`tests/test_golden_requests.py`) pass unchanged, (b) the T2 re-run from cache
(`talk-autopsy run data/test-set/T2.wav --lang ru --name T2-v3-cache`, invalid
keys in the environment) is served entirely from cache at 0 USD, and (c) all
22 files of `work/T2-v3-cache/` are byte-identical. If any of the three fails,
the change is reverted and recorded instead. Prompt files never change under
this rule.

Applied, one change at a time:

| change | frozen path | cache keys (`pipeline.code_hash`) | (a) golden | (b) T2 from cache | (c) 22 files | result |
|---|---|---|---|---|---|---|
| drop `edge-tts` | `uv.lock` (removals only) | unchanged | pass | 6 steps skipped, 0 USD | identical | done |
| remove `metrics.timing_drift` | `metrics.py` | glossary, fix, translate change | — | would re-run 3 steps | — | reverted, kept |
| remove `ingest.download` | `ingest.py` | unchanged (not hashed) | pass | 6 steps skipped, 0 USD | identical | done |

The tag `pipeline-v3.2` marks the result. Model requests and outputs are those
of `pipeline-v3`, so v1.0's measured numbers carry over without re-runs.

Why: session 2 found that the source of frozen modules is part of the cache
key (`pipeline.STEP_CODE`). A change that moves a key re-runs and re-bills
finished steps and can move published numbers; a change that keeps every key
provably changes nothing measured.

What it rules out: removing `metrics.timing_drift` now (it is listed under
Known limitations and in the Backlog); any change to a hashed module that is
"cosmetic" only.

## 2026-11-12 — Observability exception: `pipeline-v3.1` adds progress events only

Decision (owner, approved before the change): the web page's waiting screen
may get block-level progress events from frozen modules, "observability only":
model requests and pipeline outputs stay byte-identical. The change is tagged
`pipeline-v3.1`. What changed:

- `core/structured_call.py`: `result_observer`, a context variable; when set,
  it is told about every accepted result (label and parsed answer) just before
  `request_structured` returns it. An observer that raises is logged and
  ignored. Nothing else in the call changes.
- `pipeline.py`: `PipelineEvent`, a `ProgressEvent` with a `data` field. The
  `estimate`, `step-done` / `step-skipped` and `block-done` events keep their
  text (the CLI prints the same lines) and carry numbers (estimate in USD and
  tokens; cost, seconds and tokens of a finished step) and, for a finished
  correction or translation block, its segment ids and text: corrected
  segments with the model's edits (before, after), or translated sentence
  units with their English source and segment ids. The pipeline sets the
  observer around `correct.correct` and `translate.translate` only. A
  progress callback that raises is logged; the run goes on (before, the
  exception ended the run).
- `correct.py` and `translate.py` are **not** changed, although the
  exception allowed it: `pipeline.code_hash` hashes them into every stamp, so
  any byte changed there would invalidate every cached correction and
  translation and re-run (and re-bill) them. The observer in `core/`, which
  no stamp hashes, gives the same block text without touching them.

Proof:

- Golden tests (`tests/test_golden_requests.py`, committed before the change):
  every request sent to the fake model (system prompt, messages, model id,
  parameters, output schema, block boundaries) and every file the fix,
  translate and srt steps write, on an invented 12-minute talk, through
  `pipeline.run` and through direct calls. They pass unchanged after it.
- T2 from cache (`talk-autopsy run data/test-set/T2.wav --lang ru --name
  T2-v3-cache`, invalid keys in the environment): every model step skipped as
  up to date, 0 USD, all 22 files of `work/T2-v3-cache/` have the same SHA-256
  before and after, once with the new code and once with the `pipeline-v3`
  code (the change stashed). The first of these runs rewrote one file,
  `audio.wav.stamp.json`, because the source path in it was the hard link the
  first web UI smoke run had used; the audio itself hashed the same and no
  later step re-ran.
- `git diff pipeline-v3 -- talk_autopsy/prompts/` is empty.

Why: the waiting screen's text (recognised, corrected, translated) should
appear block by block, not only when a whole step ends, and the numbers should
not be parsed from text.

What it rules out: any other change to frozen paths under this exception.
v1.0's measured numbers (T2 repeats, T6, translation rating) carry over to
`pipeline-v3.1` without re-runs, because the model requests are byte-identical.

## 2026-11-12 — Release 1.0.0 private; cleanup and publication with v1.1

Decision (owner): version **1.0.0** is tagged `v1.0` as an explicit, recorded
exception to the `pipeline-v3` freeze: it changes only the package version in
`pyproject.toml` and `uv.lock`, not pipeline behaviour (T2 cached outputs
byte-identical; experiments, Freeze pipeline-v3). Removing dead code in frozen
modules (`ingest.download`, `metrics.timing_drift`) and the `edge-tts`
dependency is deferred to v1.1, when those modules are reopened for the web
interface. The repository **stays private** after `v1.0` and becomes public
with v1.1; the git-history rewrite with name replacement happens once, right
before that.

Why: the release should not reopen frozen code for cosmetic removals, and a
history rewrite is done once, when the repository goes public, not twice.

What it rules out: publishing the repository with v1.0; rewriting history or
force-pushing before v1.1; any other frozen-path change without a new tag and
hold-out.

## 2026-11-12 — Test-set sources named; speakers described only as native or non-native

Context: independent LLM review #2 (2026-11-12) found an accent label still in
a tracked file and asked how a reader could reproduce the benchmark without
knowing what was measured.

Decision (owner): the test-set sources are **named openly**: title, speaker,
event or channel, link and the cut used, in `data/test-set/README.md`, after
the owner has checked the proposed list. **No accent or nationality labels**
for any speaker, anywhere: only "native" or "non-native speaker". Transcripts
stay unpublished.

Why: the talks are public, and a benchmark that names its audio can be
rebuilt; an accent label adds nothing the measurement needs and singles a
person out.

What it rules out: publishing the reference transcripts or the media;
describing a speaker by accent or nationality. Supersedes the 2026-11-08
entry "what from the test set is published" (no titles, speakers, channels or
links) and the generic description "strong non-native accent" of 2026-11-11.

Confirmed by the owner later the same day: cite the **original public
recordings** where they were identified (T2, real-01, stress-01); our local
copies are reposts from a social network, trimmed differently, and the
reposts are not cited. Where the original was not identified (T1, T6), the
event or topic is named without a link. The names appear only in that Sources
section, the one section `scripts/check_names.py` allows them in.

## 2026-11-12 — C1 against Google Translate: accepted as a finding

Context: the owner's blind rating on 12 T6 units gave the pipeline 4.42 and
Whisper + Google Translate 4.42 (difference 0.00, bootstrap interval −0.42 …
+0.42; anchor drift 0); the specification's C1 asks for at least 1 point
(experiments, Translation rating).

Decision (owner): **accepted as a finding: not confirmed.** No translation
changes before v1.0; the translation-v4 idea stays in the Backlog.

Why: on random sentences the systems rate equal; the measured difference is
term recognition on accented speech, which v1.0 claims.

What it rules out: a new translation prompt version, or claiming better
translation than Google Translate, before v1.0.

## 2026-11-11 — VideoLingo out of v1.0 scope

Decision (owner): VideoLingo is removed from the v1.0 comparison and the
specification's Definition of Done is amended. The v1.0 comparison is the
pipeline against raw Whisper, Whisper + Google Translate and a plain Gemini
translation, on T2 and T6.

Why: VideoLingo's installer executes remote code, which the development
environment's policy does not allow; it has also since changed its
speech-recognition engine, to Qwen3-ASR.

What it rules out: a VideoLingo row with numbers in v1.0; it is reported as
"not run, out of v1.0 scope". Supersedes the VideoLingo part of the
2026-11-08 baseline entry and of the gate 2 -> 3 entry below.

## 2026-11-11 — Gate 2 -> 3 passed; v1.0 per the specification first, web UI stays v1.1

Context: the hold-out T6 ran once on `pipeline-v3` (term accuracy v2 100%
against both references, lower bound 91.67%, "pass (not demonstrated)", no
overfitting signal), and the owner rated the pre-registered translation check
blind (pipeline 4.42, plain Gemini 4.33, difference +0.08 with a bootstrap
interval of −0.33 … +0.50; rule pipeline mean >= 4.0 met). Independent LLM review #1
was done the same day (experiments, Gate 2 -> 3).

Decision (owner): **gate 2 -> 3 is passed.** Stage 3 finishes v1.0 as the
specification lists it: the baselines (raw Whisper, Whisper + Google
Translate, VideoLingo) on one set, T2 and T6; re-run from cache within a
minute; README in the series structure with honest limitations; external
review #2; tag `v1.0`. Until a web UI exists the owner starts the tool with a
Windows launcher. The web UI stays v1.1. `pipeline-v3` stays frozen;
correction stays on the main path until T3 (Backlog).

Why: the specification defines v1.0 by measurement against baselines, not by
features; the translation is only shown to be on par with a plain Gemini
translation, so the honest claim is term recognition plus the tool.

What it rules out: starting the web UI or a new prompt version before v1.0;
claiming better translation than a plain LLM translation.

## 2026-11-11 — Speaker details out of tracked files; history rewritten before going public

Context: independent LLM review #1 (finding 13) found the speakers of the test-set and
operational videos identifiable from tracked files: names, a job title,
accent labels next to product names, a course code.

Decision: tracked files carry fragment IDs only (T1, T2, T6, real-01,
stress-01) and generic descriptions ("non-native speaker"); names and
identifying descriptions were removed on 2026-11-11, and
`scripts/check_names.py` checks every tracked file against a local,
git-ignored list drawn from the owner's notes (`work/names.local.txt`). **Before
the repository is made public, its history is squashed or rewritten**, so
that old commits do not carry these details. Not done now: the repository is
private, and rewriting history before the T6 run would move the tags the run
depends on.

What it rules out: making the repository public with its current history.

## 2026-11-11 — pipeline-v3 refrozen after independent LLM review #1; T6 runs on it

Context: the review found prompt examples taken from the test set, a lenient
term-accuracy metric, no interpretation rule for T6, a cache that a
validator fix did not invalidate, and a key-term limit that raised an error.

Decision: the fixes that must precede T6 were made in one session and the
pipeline refrozen as tag `pipeline-v3` (96d9b70): `glossary-auto-v2` and
`translation-v2.1` (same rules, neutral examples), cache key with a code hash,
key-term truncation, term accuracy v2. The T6 protocol (rule, intervals,
overfitting signal, scope, translation check) was written before the run
(experiments, T6 protocol). The human check of the T6 reference that the
review asked for is not possible (no fluent listener; owner's decision); it is
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
seeing the result; the independent LLM review will see it.** Both results are
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
engine's models). The newest Pro model was the first choice, but
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
the assistant while the owner was away on 2026-11-09; the owner may
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

T1 (a conference talk with a live demo, 9:27) and T2 (a talk by a non-native
speaker, first 10:00 of 27:52) come from videos already on this machine, because
they cover the two first cases: clear speech with technical terms, and the
accented speech where the old Whisper + Google Translate chain failed. T3–T6 are
chosen on purpose to cover what is still missing: another non-native speaker, a fast screen
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
a viewer could already use. *(Out of v1.0 scope since 2026-11-11: see that
entry above.)*

## 2026-11-08 — sealed hold-out, independent LLM review, prior-art survey first

T6 is sealed: not listened to, not transcribed for prompt work, run once at the
stage 2 gate with frozen prompts, so the gate numbers are not tuned to the test
set. An independent LLM review reviews the method and results at the gate from stage 2 to
3 and again before v1.0. Before any stage is built, a survey of prior art
(VideoLingo, pyvideotrans and others) settles what already exists, so the project
does not rebuild a solved part and claims only what it adds.

## 2026-11-08 — Definition of Done with per-stage limits

A stage is done when its gate passes: WER ≤ 15% on the accented fragment for
speech recognition; term accuracy ≥ 85%, a blind translation score ≥ 4/5, the
hold-out run and an independent LLM review for correction and translation. Limits are set
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
