import type { JobSnapshot } from "../../types";

const RENDERING_JOB_STATES = new Set(["QUEUED", "RUNNING", "FINALIZING"]);

// Mirrors timestamps.ts's parseUtcMs, duplicated (rather than imported) so
// this module stays dependency-free and runnable directly under Node for
// its own tests. The backend serializes naive-but-UTC datetimes (no
// trailing "Z" or offset) — left to plain `Date.parse`, an offset-less ISO
// string is interpreted as local time instead, silently skewing any
// comparison against `Date.now()` by the browser's UTC offset (a "just
// started" job reads as hours old or hours in the future, and a clamped
// negative live elapsed time then looks permanently frozen at zero).
function parseUtcMs(value: string): number {
  const hasTimezone = /[zZ]|[+-]\d\d:?\d\d$/.test(value);
  return Date.parse(hasTimezone ? value : `${value}Z`);
}

/** The elapsed render time to display for `job` as of `nowMs`: the live
 * elapsed time while it's still rendering, or the frozen final total once it
 * succeeds. `null` before the job has actually started (still queued) and
 * for failed/partial jobs, where no duration is shown. */
export function computeRenderDurationMs(job: JobSnapshot | null, nowMs: number): number | null {
  if (!job || !job.started_at) return null;
  const startedAt = parseUtcMs(job.started_at);
  if (job.state === "SUCCEEDED" && job.finished_at) {
    return Math.max(0, parseUtcMs(job.finished_at) - startedAt);
  }
  if (RENDERING_JOB_STATES.has(job.state)) return Math.max(0, nowMs - startedAt);
  return null;
}

export function isRenderingJob(job: JobSnapshot | null): boolean {
  return Boolean(job && RENDERING_JOB_STATES.has(job.state) && job.started_at);
}

export function formatElapsedSeconds(ms: number): string {
  const totalSeconds = Math.floor(ms / 1_000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}
