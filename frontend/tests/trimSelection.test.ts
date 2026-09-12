import assert from "node:assert/strict";

import { nudgeStepMs } from "../src/features/make-clip/boundaryNudges.ts";
import {
  clampTrimRange,
  shouldStopPreview,
  validateTrimValue,
} from "../src/features/trim-clip/trimSelection.ts";

assert.deepEqual(clampTrimRange(5_000, 5_000, 10_000, "start"), {
  startMs: 4_999,
  endMs: 5_000,
});
assert.deepEqual(clampTrimRange(5_000, 5_000, 10_000, "end"), {
  startMs: 5_000,
  endMs: 5_001,
});
assert.deepEqual(clampTrimRange(-20, 12_000, 10_000, "end"), {
  startMs: 0,
  endMs: 10_000,
});

assert.deepEqual(validateTrimValue({ value: 1, error: null }, "start", 2, 10_000), {
  value: 1,
  error: null,
});
assert.match(validateTrimValue({ value: 2_000, error: null }, "start", 2_000, 10_000).error ?? "", /earlier/);
assert.match(validateTrimValue({ value: 10_001, error: null }, "end", 0, 10_000).error ?? "", /within/);

assert.equal(nudgeStepMs("frames", 5, 24000 / 1001), 209);
assert.equal(nudgeStepMs("seconds", 5, null), 5_000);
assert.equal(shouldStopPreview(1.999, 2_000), false);
assert.equal(shouldStopPreview(2, 2_000), true);

// A trim/extend dialog widens the editable range below 0 once room has been
// granted before the clip's original start — clampTrimRange must accept and
// preserve a negative floor instead of clamping everything back to 0.
assert.deepEqual(clampTrimRange(-3_000, 7_500, 10_000, "start", -5_000), {
  startMs: -3_000,
  endMs: 7_500,
});
assert.deepEqual(clampTrimRange(-9_000, 7_500, 10_000, "start", -5_000), {
  startMs: -5_000,
  endMs: 7_500,
});
assert.deepEqual(clampTrimRange(-3_000, -3_000, 10_000, "start", -5_000), {
  startMs: -3_001,
  endMs: -3_000,
});
// Omitting rangeStartMs still behaves exactly as before (floor of 0).
assert.deepEqual(clampTrimRange(-20, 12_000, 10_000, "end"), {
  startMs: 0,
  endMs: 10_000,
});

console.log("trim selection tests passed");
