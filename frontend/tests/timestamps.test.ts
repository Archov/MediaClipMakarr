import assert from "node:assert/strict";

import { formatTimestampMs, parseTimestampMs, parseUtcMs } from "../src/timestamps.ts";

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

// The backend serializes naive-but-UTC datetimes (no trailing "Z"/offset) —
// parseUtcMs must treat those as UTC rather than letting Date.parse assume
// local time, while still respecting a string that already carries a zone.
assert.equal(parseUtcMs("2026-01-01T00:00:00"), Date.parse("2026-01-01T00:00:00Z"));
assert.equal(parseUtcMs("2026-01-01T00:00:00.123456"), Date.parse("2026-01-01T00:00:00.123456Z"));
assert.equal(parseUtcMs("2026-01-01T00:00:00Z"), Date.parse("2026-01-01T00:00:00Z"));
assert.equal(parseUtcMs("2026-01-01T00:00:00+02:00"), Date.parse("2026-01-01T00:00:00+02:00"));
