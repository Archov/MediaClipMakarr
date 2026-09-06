import assert from "node:assert/strict";

import {
  computeRenderDurationMs,
  formatElapsedSeconds,
} from "../src/features/make-clip/renderDuration.ts";
import type { JobSnapshot } from "../src/types.ts";

function job(overrides: Partial<JobSnapshot>): JobSnapshot {
  return {
    id: "job-1",
    type: "clip_create",
    state: "RUNNING",
    stage: "rendering",
    progress: 0.5,
    current_stage_progress: 0.5,
    elapsed_ms: null,
    queue_position: null,
    message: "Rendering.",
    result: null,
    error: null,
    created_at: "2026-01-01T00:00:00",
    started_at: "2026-01-01T00:00:00",
    finished_at: null,
    ...overrides,
  };
}

const startedAtMs = Date.parse("2026-01-01T00:00:00Z");

// Still queued (no started_at yet): nothing to show.
assert.equal(computeRenderDurationMs(job({ state: "QUEUED", started_at: null }), startedAtMs), null);

// Rendering: ticks live against the caller-supplied "now".
assert.equal(
  computeRenderDurationMs(job({ state: "RUNNING", started_at: "2026-01-01T00:00:00Z" }), startedAtMs + 7_000),
  7_000,
);

// Succeeded: frozen at the final started/finished span, ignoring "now".
assert.equal(
  computeRenderDurationMs(
    job({
      state: "SUCCEEDED",
      started_at: "2026-01-01T00:00:00Z",
      finished_at: "2026-01-01T00:00:42Z",
    }),
    startedAtMs + 999_000,
  ),
  42_000,
);

// Failed/partial jobs never show a duration, even once started.
assert.equal(
  computeRenderDurationMs(job({ state: "FAILED", started_at: "2026-01-01T00:00:00Z" }), startedAtMs + 5_000),
  null,
);

assert.equal(computeRenderDurationMs(null, startedAtMs), null);

// Regression: the backend actually serializes naive-but-UTC timestamps (no
// trailing "Z"), which plain `Date.parse` would misread as local time —
// skewing a live "now - started_at" comparison by the browser's UTC offset
// (a live counter permanently clamped to zero was the reported symptom).
// `nowMs` here is real UTC epoch ms, exactly like `Date.now()` in the
// browser, deliberately NOT derived from the same naive string.
assert.equal(
  computeRenderDurationMs(
    job({ state: "RUNNING", started_at: "2026-01-01T00:00:00" }),
    Date.parse("2026-01-01T00:00:07Z"),
  ),
  7_000,
);

assert.equal(formatElapsedSeconds(0), "0:00");
assert.equal(formatElapsedSeconds(7_000), "0:07");
assert.equal(formatElapsedSeconds(59_999), "0:59");
assert.equal(formatElapsedSeconds(60_000), "1:00");
assert.equal(formatElapsedSeconds(125_000), "2:05");

console.log("render duration tests passed");
