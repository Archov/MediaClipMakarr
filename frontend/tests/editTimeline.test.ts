import assert from "node:assert/strict";

import {
  canShiftTimelineBoundary,
  clampTimelineSelection,
  createTimelineTicks,
  effectiveEditableRange,
  formatTimelineRulerTime,
  pointerPositionToTime,
  shiftTimelineBoundary,
  timeToTimelinePercent,
  visibleTimelineIntersection,
  type TimelineRange,
} from "../src/features/editing/timelineMath.ts";

const viewportRange: TimelineRange = { startMs: 10_000, endMs: 20_000 };
const widerEditableRange: TimelineRange = { startMs: 0, endMs: 30_000 };

assert.deepEqual(effectiveEditableRange(viewportRange), viewportRange);
assert.deepEqual(effectiveEditableRange(viewportRange, widerEditableRange), widerEditableRange);

assert.equal(pointerPositionToTime(150, 100, 200, viewportRange), 12_500);
assert.equal(pointerPositionToTime(50, 100, 200, viewportRange), 10_000);
assert.equal(pointerPositionToTime(350, 100, 200, viewportRange), 20_000);
assert.equal(timeToTimelinePercent(15_000, viewportRange), 50);

assert.deepEqual(
  visibleTimelineIntersection({ startMs: 5_000, endMs: 12_000 }, viewportRange),
  { startMs: 10_000, endMs: 12_000 },
);
assert.deepEqual(
  visibleTimelineIntersection({ startMs: 18_000, endMs: 25_000 }, viewportRange),
  { startMs: 18_000, endMs: 20_000 },
);
assert.equal(visibleTimelineIntersection({ startMs: 0, endMs: 5_000 }, viewportRange), null);

assert.deepEqual(
  clampTimelineSelection({ startMs: -5_000, endMs: 35_000 }, widerEditableRange, "end"),
  { startMs: 0, endMs: 30_000 },
);
assert.deepEqual(
  clampTimelineSelection({ startMs: 30_000, endMs: 30_000 }, widerEditableRange, "end"),
  { startMs: 29_999, endMs: 30_000 },
);
assert.deepEqual(
  clampTimelineSelection({ startMs: 0, endMs: 0 }, widerEditableRange, "start"),
  { startMs: 0, endMs: 1 },
);

const selectionRange = { startMs: 11_000, endMs: 19_000 };
assert.equal(canShiftTimelineBoundary(selectionRange, widerEditableRange, "start", -40), true);
assert.deepEqual(
  shiftTimelineBoundary(selectionRange, widerEditableRange, "start", -40),
  { startMs: 10_960, endMs: 19_000 },
);
assert.equal(canShiftTimelineBoundary(selectionRange, widerEditableRange, "start", 8_000), false);
assert.equal(canShiftTimelineBoundary(selectionRange, widerEditableRange, "end", -8_000), false);
assert.equal(
  shiftTimelineBoundary(selectionRange, widerEditableRange, "end", -8_000),
  selectionRange,
);

const narrowTicks = createTimelineTicks(viewportRange, 320);
const wideTicks = createTimelineTicks(viewportRange, 1_200);
assert.ok(narrowTicks.some((tick) => tick.major));
assert.ok(wideTicks.filter((tick) => tick.major).length > narrowTicks.filter((tick) => tick.major).length);
assert.equal(formatTimelineRulerTime(754_000), "12:34");
assert.equal(formatTimelineRulerTime(3_754_000), "1:02:34");

console.log("edit timeline tests passed");
