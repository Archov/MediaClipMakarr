import assert from "node:assert/strict";

import { formatTimestampMs, parseTimestampMs } from "../src/timestamps.ts";

assert.equal(formatTimestampMs(0), "00:00:00.000");
assert.equal(formatTimestampMs(3_723_004), "01:02:03.004");
assert.deepEqual(parseTimestampMs("01:02:03.004"), {
  value: 3_723_004,
  error: null,
});
assert.deepEqual(parseTimestampMs("12:34:56.789"), {
  value: 45_296_789,
  error: null,
});
assert.equal(formatTimestampMs(parseTimestampMs("99:59:59.999").value), "99:59:59.999");
assert.equal(parseTimestampMs("-01:00:00.000").error, "Use HH:MM:SS.mmm.");
assert.equal(parseTimestampMs("00:60:00.000").error, "Use HH:MM:SS.mmm.");
