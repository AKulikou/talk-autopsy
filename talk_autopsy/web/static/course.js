// Course helpers for the page (app.js), free of the DOM so they run in Node (tests/js/course.test.mjs).

// The positions a range "from lesson N to lesson M" selects: available lessons only, either order.
export function rangeSelection(lessons, from, to) {
  const a = Number.parseInt(from, 10);
  const b = Number.parseInt(to, 10);
  if (!Number.isFinite(a) && !Number.isFinite(b)) return [];
  const low = Math.min(Number.isFinite(a) ? a : b, Number.isFinite(b) ? b : a);
  const high = Math.max(Number.isFinite(a) ? a : b, Number.isFinite(b) ? b : a);
  return lessons.filter((l) => l.available && l.position >= low && l.position <= high).map((l) => l.position);
}

export function allSelection(lessons) {
  return lessons.filter((l) => l.available).map((l) => l.position);
}

// "Lesson n of count": n is the running lesson's place among the course's lessons (or the next one).
export function lessonPlace(course) {
  const lessons = course?.lessons || [];
  const count = lessons.length;
  const running = lessons.findIndex((l) => l.status === "running");
  if (running >= 0) return { n: running + 1, count };
  const queued = lessons.findIndex((l) => l.status === "queued");
  return { n: queued >= 0 ? queued + 1 : count, count };
}

// The overall bar: finished lessons (done or failed or skipped) plus the running one's share.
export function courseFraction(course, runningShare = 0) {
  const lessons = course?.lessons || [];
  if (!lessons.length) return 0;
  const done = lessons.filter((l) => ["finished", "failed", "skipped"].includes(l.status)).length;
  const running = lessons.some((l) => l.status === "running") ? Math.min(Math.max(runningShare, 0), 0.99) : 0;
  return Math.min(1, (done + running) / lessons.length);
}

export function failedPositions(course) {
  return (course?.lessons || []).filter((l) => l.status === "failed").map((l) => l.position);
}

export function leftPositions(course) {
  return (course?.lessons || []).filter((l) => ["queued", "running"].includes(l.status)).map((l) => l.position);
}
