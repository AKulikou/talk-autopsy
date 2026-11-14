"""
A course (a YouTube playlist) run through the web page's own orchestration (web/courses.py:
CourseRunner -> JobRunner -> the pipeline), without a browser: the same lesson jobs, folders,
course.json, cost gate and course glossary as "Start the course" in the page.

    uv run python scripts/course_run.py <playlist link> --lessons 1-3 --dry-run
    uv run python scripts/course_run.py <playlist link> --lessons 1-3 --profile all --domain web3 \\
        --course-glossary off --output-root work/C2b/without/output [--confirm 3.40]

Used for the C2b measurement and the owner's course (docs/experiments.md). `--output-root` puts the
course folder elsewhere (the pipeline's work folders, and so the cache, stay in work/). Events are
recorded to logs/web/events/ as the page's jobs are. Prints the course's state every 20 s.
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def positions(text: str) -> list[int] | None:
    """'1-3,5' -> [1, 2, 3, 5]; 'all' -> None."""
    if text == "all":
        return None
    out: list[int] = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return sorted(set(out))


def main() -> None:
    from talk_autopsy import config
    from talk_autopsy.web import jobs
    from talk_autopsy.web.courses import CourseRequest, CourseRunner
    from talk_autopsy.web.jobs import GateError, JobRunner

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("url")
    parser.add_argument("--lessons", default="all", help="playlist positions: 1-3,5 or all")
    parser.add_argument("--profile", default="all", choices=("tv", "laptop", "all"))
    parser.add_argument("--domain", default="none", choices=("none", "web3"))
    parser.add_argument("--lang", default="ru")
    parser.add_argument("--course-glossary", choices=("on", "off"), default="off")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--confirm", type=float, help="the total to confirm when it is above the limit")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    config.load_environment()

    class Core(jobs.Core):
        def output_root(self) -> Path:
            return (args.output_root or config.output_dir()).resolve()

    runner = JobRunner(core=Core(), record_dir=config.LOGS_DIR / "web" / "events")
    courses = CourseRunner(runner)
    request = CourseRequest(url=args.url, profile=args.profile, domain=args.domain, target_lang=args.lang,
                            course_glossary=args.course_glossary == "on", selected=positions(args.lessons),
                            confirmed_cost_usd=args.confirm)
    preview = courses.preview(request)
    data = preview.as_dict(request.selected, runner.limit_usd)
    print(f"Course: {data['title']} ({len(data['lessons'])} lessons) -> {data['folder']}")
    for lesson in data["lessons"]:
        mark = "x" if lesson["position"] in data["selected"] else " "
        est = lesson["estimate"]
        note = lesson["reason"] or ("already done" if lesson["ready"] else "")
        print(f"  [{mark}] {lesson['position']:3} {lesson['title'][:60]:<60} {lesson['duration_s'] or 0:5}s "
              f"{est['claude_usd']:.3f}+{est['stt_usd']:.3f} USD {note}")
    totals = data["totals"]
    print(f"Selected {totals['count']}: Claude {totals['claude_usd']:.4f} + speech recognition "
          f"{totals['stt_usd']:.4f} = {totals['total_usd']:.2f} USD, about {totals['time_s']} s; "
          f"confirmation needed: {data['needs_confirmation']}")
    if args.dry_run:
        return
    try:
        course = courses.start(request)
    except GateError as e:
        sys.exit(f"Not started: {e}")
    while courses.active is not None:
        time.sleep(20)
        s = course.summary(runner.limit_usd)
        print(f"{time.strftime('%H:%M:%S')} lesson {s['current']}, done {s['done']}/{s['count']}, "
              f"failed {s['failed']}, spent {s['cost_usd']:.4f} + {s['stt_cost_usd']:.4f} USD", flush=True)
    s = course.summary(runner.limit_usd)
    print(f"Course {s['state']}: {s['finished']} finished, {s['failed']} failed of {s['count']}; "
          f"Claude {s['cost_usd']:.4f} USD, speech recognition {s['stt_cost_usd']:.4f} USD")
    for lesson in s["lessons"]:
        print(f"  {lesson['position']:3} {lesson['status']:<9} {lesson['cost_usd']:.4f} + "
              f"{lesson['stt_cost_usd']:.4f} USD  {lesson['folder']}"
              + (f"  [{(lesson['error'] or {}).get('reason')}]" if lesson["error"] else ""))


if __name__ == "__main__":
    main()
