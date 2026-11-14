"""Building the test set's references: `adjudicate`, `resolve`, `judge`, `judge-compare`, `judge-refs`,
`glossary-ref`."""

import json
from collections import Counter
from pathlib import Path
from typing import Annotated

import typer

from talk_autopsy import adjudicate as consensus_module
from talk_autopsy import config, ingest, judge, metrics
from talk_autopsy.cli.common import (
    ForceOption,
    check_fragment,
    consensus_path,
    fail,
    judged_path,
    majority_ref_path,
    uncertain_spans_path,
    write_new,
)
from talk_autopsy.config import ConfigurationError
from talk_autopsy.glossary import GlossaryError, load_glossary
from talk_autopsy.segments import load_transcript


def adjudicate_command(
    fragment: Annotated[str, typer.Argument(help="Test-set fragment ID, e.g. T2.")],
    backbone: Annotated[Path, typer.Option(help="Transcript with word timings (faster-whisper).")],
    hyp: Annotated[list[Path], typer.Option(help="Cloud transcript; give it twice (AssemblyAI, Gemini).")],
    audio: Annotated[Path | None, typer.Option(
        help="Audio for the clips. Default: data/test-set/<FRAGMENT>.wav.")] = None,
    force: ForceOption = False,
) -> None:
    """Align three transcripts; write the adjudication file and one audio clip per disagreement."""
    fragment = check_fragment(fragment)
    if len(hyp) != 2:
        raise typer.BadParameter(f"give --hyp exactly twice, got {len(hyp)}", param_hint="--hyp")
    audio = audio or config.TEST_SET_DIR / f"{fragment}.wav"
    out = config.TEST_SET_DIR / f"{fragment}.adjudication.md"
    if out.exists() and not force:
        fail(f"{out} exists; it may hold your choices. Pass --force to overwrite it.")
    try:
        transcripts = [load_transcript(path) for path in (backbone, *hyp)]
        seconds = ingest.probe(audio)
        result = consensus_module.build_consensus(fragment, transcripts[0], transcripts[1:], seconds)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        fail(str(e))
    clip_dir = config.WORK_DIR / fragment / "clips"
    for old in clip_dir.glob("*.wav"):  # clips of an earlier alignment would no longer match the numbers
        old.unlink()
    try:
        clips = consensus_module.write_clips(result, audio, clip_dir)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        fail(str(e))
    consensus_file = consensus_path(fragment)
    consensus_file.parent.mkdir(parents=True, exist_ok=True)
    consensus_file.write_text(consensus_module.consensus_to_json(result), encoding="utf-8", newline="\n")
    shown = f"work/{fragment}/clips"
    text = consensus_module.adjudication_markdown(result, shown, link_prefix="../../")
    write_new(out, text, force=True)
    c = result.counts()
    typer.echo(f"Wrote {out}: {c['words']} words, {c['unanimous_share'] * 100:.1f}% unanimous, "
               f"{c['majority_spans']} majority and {c['split_spans']} split spans, "
               f"{len(clips)} clips ({c['clip_minutes']} min) in {clip_dir}")


def resolve_command(
    fragment: Annotated[str, typer.Argument(help="Test-set fragment ID, e.g. T2.")],
    out: Annotated[Path | None, typer.Option(
        "--out", "-o", help="Reference to write. Default: data/test-set/<FRAGMENT>.ref.txt.")] = None,
    adjudication: Annotated[Path | None, typer.Option(
        help="Filled adjudication file. Default: data/test-set/<FRAGMENT>.adjudication.md "
             "(the judge's copy: <FRAGMENT>.adjudication.judged.md).")] = None,
    force: ForceOption = False,
    normalize_adjudication: Annotated[bool, typer.Option(
        "--normalize-adjudication",
        help="Pass every span's chosen text through the adjudication normaliser (repetitions written "
             "once, thousands separators, key combinations); for a reference built from a judged file.",
    )] = False,
) -> None:
    """Compose the reference from the agreed words and the variants chosen in the adjudication file."""
    fragment = check_fragment(fragment)
    out = out or config.TEST_SET_DIR / f"{fragment}.ref.txt"
    adjudication = adjudication or config.TEST_SET_DIR / f"{fragment}.adjudication.md"
    consensus_file = consensus_path(fragment)
    for path in (adjudication, consensus_file):
        if not path.is_file():
            fail(f"No {path}; run talk-autopsy adjudicate {fragment} first")
    try:
        result = consensus_module.consensus_from_json(consensus_file.read_text(encoding="utf-8"))
        choices = consensus_module.parse_choices(adjudication.read_text(encoding="utf-8-sig"))
        resolution = consensus_module.resolve(
            result, choices, postprocess=judge.normalize_for_adjudication if normalize_adjudication else None)
    except (ValueError, TypeError, KeyError) as e:
        fail(str(e))
    write_new(out, resolution.reference, force)
    protocol_path = out.with_name(out.name.removesuffix(".txt") + ".protocol.json")
    protocol_path.write_text(json.dumps(resolution.protocol, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8", newline="\n")
    t = resolution.protocol["totals"]
    typer.echo(f"Wrote {out}: {t['reference_words']} words")
    typer.echo(f"  from consensus (all engines agree): {t['consensus_words']} words")
    for engine in resolution.protocol["engines"]:
        typer.echo(f"  chosen variant had {engine:<16} {t['words_by_engine'][engine]} words "
                   f"in {t['spans_by_engine'][engine]} spans")
    typer.echo(f"  typed by the owner: {t['typed_words']} words in {t['spans_typed']} spans")
    typer.echo(f"  majority spans kept / changed: {t['majority_kept']} / {t['majority_changed']}")
    typer.echo(f"Wrote {protocol_path}")


def judge_command(
    fragment: Annotated[str, typer.Argument(help="Test-set fragment ID, e.g. T2.")],
    model: Annotated[str | None, typer.Option(
        help="Gemini model. Default: JUDGE_MODEL (gemini-4-carbon); never the STT engine's model.")] = None,
    limit: Annotated[int | None, typer.Option(min=1, help="Judge at most N spans in this run.")] = None,
    resume: Annotated[bool, typer.Option("--resume", help="Continue work/<FRAGMENT>/judge.jsonl.")] = False,
    pace: Annotated[float, typer.Option(min=0.0, help="Seconds between request starts.")] = (
        judge.PACE_SECONDS),
    budget: Annotated[float, typer.Option(
        min=0.0, help="Session budget in USD (the run's own counter).")] = config.JUDGE_BUDGET_USD,
    spent_before: Annotated[float, typer.Option(
        min=0.0, help="USD the session spent before this run.")] = 0.0,
    plan_after: Annotated[int, typer.Option(
        min=0, help="Spans of later work in the same budget, for the projection (e.g. T6 after T2).")] = 0,
) -> None:
    """
    Blind audio judge: Gemini listens to every majority and split span's clip, transcribes it,
    then picks one of the shuffled unlabeled variants or types its own text. Writes
    work/<FRAGMENT>/judge.jsonl and data/test-set/<FRAGMENT>.adjudication.judged.md. Prints counts
    and cost only.
    """
    import datetime

    fragment = check_fragment(fragment)
    adjudication = config.TEST_SET_DIR / f"{fragment}.adjudication.md"
    consensus_file = consensus_path(fragment)
    for path in (adjudication, consensus_file):
        if not path.is_file():
            fail(f"No {path}; run talk-autopsy adjudicate {fragment} first")
    model = model or config.judge_model()
    if model in config.gemini_models():
        fail(f"{model} is the Gemini STT engine's model; the judge must be a different model")
    thinking = config.judge_thinking_level()
    typer.echo(f"Judge {model}, thinking level {thinking}")
    out = config.WORK_DIR / fragment / "judge.jsonl"
    try:
        result = consensus_module.consensus_from_json(consensus_file.read_text(encoding="utf-8"))
        conventions = judge.read_conventions((config.TEST_SET_DIR / "README.md").read_text(encoding="utf-8"))
        summary = judge.run(fragment, result, config.WORK_DIR / fragment / "clips", conventions, out, model,
                            limit=limit, resume=resume, pace_seconds=pace, progress=typer.echo,
                            thinking_level=thinking, budget_usd=budget, spent_before_usd=spent_before,
                            plan_after=plan_after)
        records = judge.load_records(out)
    except (FileNotFoundError, ValueError, RuntimeError, ConfigurationError) as e:
        fail(str(e))
    filled = judge.filled_markdown(adjudication.read_text(encoding="utf-8-sig"), records, summary.total,
                                   datetime.date.today().isoformat())
    judged = judged_path(fragment)
    judged.write_text(filled, encoding="utf-8", newline=chr(10))
    stats = judge.run_stats(records)
    typer.echo(f"Judge {model}: {summary.done} of {summary.total} spans judged "
               f"({summary.judged_now} in this run), {summary.remaining} remaining")
    if records:
        typer.echo(f"  chose the Gemini engine's variant: {stats['gemini_chosen']} "
                   f"({stats['gemini_share']:.1%})")
        typer.echo(f"  none of the options: {stats['none']} ({stats['none_share']:.1%})")
        if stats["consistency_share"] is not None:
            typer.echo(f"  heard contains the chosen text: {stats['consistent']} of "
                       f"{stats['consistency_checked']} ({stats['consistency_share']:.1%})")
        typer.echo(f"  heard written before choice: {stats['heard_first_share']:.1%}")
        typer.echo(f"  cost of the recorded spans: ${stats['cost_usd']:.4f}")
    session = spent_before + summary.spent_usd
    typer.echo(f"Spent in this run: ${summary.spent_usd:.4f} (session ${session:.4f} of ${budget:.2f})")
    typer.echo(f"Wrote {out} and {judged}")
    if summary.failed:
        typer.echo(f"  {len(summary.failed)} spans failed twice and are left for the next run")
    if summary.stopped:
        typer.echo(f"Stopped early: {summary.stopped}")
    if summary.remaining:
        typer.echo(f"Rerun `talk-autopsy judge {fragment} --resume` "
                   "(after the daily reset if the quota ran out).")
        raise typer.Exit(code=2)


def judge_compare_command(
    fragment: Annotated[str, typer.Argument(help="Test-set fragment ID with decided spans, e.g. T2.")],
    normalize_adjudication: Annotated[bool, typer.Option(
        "--normalize-adjudication",
        help="Compare after the adjudication normaliser on both sides (post-hoc rescoring, 2026-11-10); "
             "writes judge.compare.normalized.json.")] = False,
) -> None:
    """
    Agreement of the judge's file with the decided adjudication file, overall and by how each
    span was decided (inferred), and the WER between the decided reference and one resolved from
    the judged file. Prints span texts: never use it on a sealed fragment.
    """
    fragment = check_fragment(fragment)
    if fragment in config.SEALED_FRAGMENTS:
        fail(f"{fragment} is sealed; judge-compare prints span texts")
    normalize = judge.normalize_for_adjudication if normalize_adjudication else None
    try:
        result = consensus_module.consensus_from_json(consensus_path(fragment).read_text(encoding="utf-8"))
        reference = consensus_module.parse_choices(
            (config.TEST_SET_DIR / f"{fragment}.adjudication.md").read_text(encoding="utf-8-sig"))
        judged = consensus_module.parse_choices(judged_path(fragment).read_text(encoding="utf-8-sig"))
        glossary_file = config.TEST_SET_DIR / f"{fragment}.glossary.md"
        terms = [e.term for e in load_glossary(glossary_file).entries] if glossary_file.is_file() else []
        ear_file = config.WORK_DIR / fragment / "owner-by-ear.json"  # the owner's list, if recorded
        by_ear = ({int(s["span"]) for s in json.loads(ear_file.read_text(encoding="utf-8"))["spans"]}
                  if ear_file.is_file() else set())
        report = judge.compare(result, reference, judged, terms, by_ear, normalize=normalize)
        decided_ref = consensus_module.resolve(result, reference).reference
        judged_ref = consensus_module.resolve(result, judged, postprocess=normalize).reference
        records = {r["span"]: r for r in judge.load_records(config.WORK_DIR / fragment / "judge.jsonl")}
    except (FileNotFoundError, ValueError, RuntimeError, KeyError) as e:
        fail(str(e))
    if normalize:  # the decided reference passes through the same normaliser: both sides alike
        decided_ref = normalize(decided_ref)
        judged_ref = normalize(judged_ref)
    wer = metrics.word_error_rate(decided_ref, judged_ref)
    report["references_wer"] = {"wer": round(wer.wer, 4), "substitutions": wer.substitutions,
                                "deletions": wer.deletions, "insertions": wer.insertions,
                                "reference_words": wer.reference_words}

    def line(name: str, s: dict) -> str:
        share = "-" if s["share"] is None else f"{s['share']:.1%}"
        return f"  {name:<22} {s['agree']:>3} of {s['spans']:<3} {share:>7}"

    typer.echo(f"{fragment}: judge against the decided spans (normalized text equal)")
    typer.echo(line("overall", report["overall"]))
    for name, s in report["by_class"].items():
        typer.echo(line(f"decided by {name}", s))
    for name, s in report["by_verdict"].items():
        typer.echo(line(f"{name} spans", s))
    typer.echo(f"WER between the decided reference and the judged one: {wer.wer:.2%} "
               f"({wer.reference_words} words; {wer.substitutions} S, {wer.deletions} D, {wer.insertions} I)")
    typer.echo("Disagreements (span, class, decided | judge):")
    for row in report["rows"]:
        if not row["agree"]:
            typer.echo(f"  {row['span']:04d} {row['class']:<8} {row['reference'] or '(no words)'} | "
                       f"{row['judge'] or '(no words)'}")
            if normalize:
                record = records.get(row["span"], {})
                typer.echo(f"       [{row['disagreement']}] heard: {record.get('heard', '?')!r}; "
                           f"final_text: {record.get('final_text_raw', '?')!r}")
    if normalize:
        counts = Counter(r["disagreement"] for r in report["rows"] if not r["agree"])
        typer.echo("Remaining disagreements by class: "
                   + ", ".join(f"{c} {counts.get(c, 0)}" for c in ("term", "word", "formatting")))
    name = "judge.compare.normalized.json" if normalize else "judge.compare.json"
    out = config.WORK_DIR / fragment / name
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + chr(10), encoding="utf-8",
                   newline=chr(10))
    typer.echo(f"Wrote {out}")


def judge_refs_command(
    fragment: Annotated[str, typer.Argument(help="Test-set fragment ID judged by the audio judge, e.g. T6.")],
    force: ForceOption = False,
) -> None:
    """
    Two references from the judge's records: J (the judge's text in every judged span) ->
    <FRAGMENT>.ref.txt, M (the engines' majority where one exists, the judge on splits) ->
    <FRAGMENT>.ref.majority.txt, and work/<FRAGMENT>/uncertain_spans.json (split spans plus majority
    spans where J and M differ). With <FRAGMENT>.glossary.md present, also counts its term occurrences
    in J and inside uncertain spans. Prints counts only.
    """
    import datetime

    from talk_autopsy import ref_bounds

    fragment = check_fragment(fragment)
    consensus_file = consensus_path(fragment)
    records_file = config.WORK_DIR / fragment / "judge.jsonl"
    for path in (consensus_file, records_file):
        if not path.is_file():
            fail(f"No {path}; run talk-autopsy adjudicate and judge {fragment} first")
    out_j = config.TEST_SET_DIR / f"{fragment}.ref.txt"
    out_m = majority_ref_path(fragment)
    try:
        result = consensus_module.consensus_from_json(consensus_file.read_text(encoding="utf-8"))
        records = judge.load_records(records_file)
        refs = ref_bounds.build(result, records)
    except (ValueError, KeyError, RuntimeError) as e:
        fail(str(e))
    for path, text in ((out_j, refs.j.reference), (out_m, refs.m.reference)):
        if path.exists() and not force and path.read_text(encoding="utf-8") != text:
            fail(f"{path} exists with other text. Pass --force to overwrite it.")
    for path, resolution in ((out_j, refs.j), (out_m, refs.m)):
        path.write_text(resolution.reference, encoding="utf-8", newline=chr(10))
        protocol = path.with_name(path.name.removesuffix(".txt") + ".protocol.json")
        protocol.write_text(json.dumps(resolution.protocol, ensure_ascii=False, indent=2) + chr(10),
                            encoding="utf-8", newline=chr(10))
    model = records[0].get("model", "?")
    uncertain_file = uncertain_spans_path(fragment)
    uncertain_file.write_text(
        ref_bounds.uncertain_json(fragment, refs, model, out_j.as_posix(), out_m.as_posix(),
                                  datetime.date.today().isoformat()),
        encoding="utf-8", newline=chr(10))
    c = refs.counts
    typer.echo(f"{fragment}: two references from {len(records)} spans judged by {model}")
    typer.echo(f"  spans judged: majority {c['judged']['majority']}, split {c['judged']['split']}")
    typer.echo(f"  majority spans where the judge overrode the majority: {c['majority_overridden']}")
    typer.echo(f"  uncertain spans (split + overridden majority): {c['uncertain_spans']}")
    typer.echo(f"  judged spans whose final_text differs from the chosen option: "
               f"{c['final_text_differs_from_chosen_option']}")
    w = c["wer_j_m"]
    typer.echo(f"  words: J {c['words_j']}, M {c['words_m']}; WER of M against J {w['wer']:.2%} "
               f"({w['substitutions']} S, {w['deletions']} D, {w['insertions']} I)")
    glossary_file = config.TEST_SET_DIR / f"{fragment}.glossary.md"
    if glossary_file.is_file():
        try:
            terms = [e.term for e in load_glossary(glossary_file).entries]
            ranges = [tuple(s["J"]["words"]) for s in refs.uncertain]
            total, inside = ref_bounds.term_occurrences_inside(refs.j.reference, terms, ranges)
        except (ValueError, GlossaryError) as e:
            fail(str(e))
        typer.echo(f"  glossary: {len(terms)} terms; {total} occurrences in J, "
                   f"{inside} inside uncertain spans")
    typer.echo(f"Wrote {out_j}, {out_m} (+ protocols) and {uncertain_file}")


def glossary_ref_command(
    fragment: Annotated[str, typer.Argument(help="Test-set fragment ID with a reference, e.g. T6.")],
    model: Annotated[str | None, typer.Option(
        help="Gemini model. Default: JUDGE_MODEL (gemini-4-carbon).")] = None,
    prompt: Annotated[str, typer.Option(
        help="Prompt version: v1 (T6: 30-60 terms, free category) or v2 (T7: 60-90 terms, one of six "
             "categories, no names of people).")] = "v1",
    force: ForceOption = False,
) -> None:
    """
    Reference glossary drafted by Gemini (not Claude) from data/test-set/<FRAGMENT>.ref.txt:
    terms that do not occur in the reference are dropped. Writes <FRAGMENT>.glossary.md. Counts only.
    """
    if prompt not in ("v1", "v2"):
        raise typer.BadParameter("use v1 or v2", param_hint="--prompt")
    import datetime

    from talk_autopsy import ref_glossary

    fragment = check_fragment(fragment)
    reference = config.TEST_SET_DIR / f"{fragment}.ref.txt"
    out = config.TEST_SET_DIR / f"{fragment}.glossary.md"
    if not reference.is_file():
        fail(f"No {reference}; resolve {fragment} first")
    if out.exists() and not force:
        fail(f"{out} exists. Pass --force to overwrite it.")
    try:
        extraction = ref_glossary.extract(reference.read_text(encoding="utf-8"),
                                          model or config.judge_model(),
                                          thinking_level=config.judge_thinking_level(), version=prompt)
    except (ValueError, RuntimeError, ConfigurationError) as e:
        fail(str(e))
    out.write_text(ref_glossary.glossary_file(fragment, extraction, datetime.date.today().isoformat()),
                   encoding="utf-8", newline=chr(10))
    fewest, most = ref_glossary.limits(prompt)
    kept = len(extraction.glossary.entries)
    typer.echo(f"Wrote {out}: {kept} terms ({extraction.proposed} proposed by {extraction.model}, "
               f"{extraction.dropped_absent} not in the reference, {extraction.dropped_over_limit} over "
               f"the limit of {most}; {extraction.prompt_version})")
    typer.echo(f"  cost ${extraction.cost_usd:.4f} ({extraction.tokens['prompt']} input, "
               f"{extraction.tokens['output']} output, {extraction.tokens['thinking']} thinking tokens)")
    if prompt == "v2":
        counts = {c: 0 for c in ref_glossary.CATEGORIES}
        for entry in extraction.glossary.entries:
            counts[entry.notes] = counts.get(entry.notes, 0) + 1
        typer.echo("  categories: " + ", ".join(f"{c} {n}" for c, n in counts.items()))
    if kept < fewest:
        typer.echo(f"  fewer than {fewest} terms")
