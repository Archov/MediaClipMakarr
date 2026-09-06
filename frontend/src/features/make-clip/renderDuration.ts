import type { JobSnapshot } from "../../types";

const RENDERING_JOB_STATES = new Set(["QUEUED", "RUNNING", "FINALIZING"]);

/** The elapsed render time to display for `job` as of `nowMs`: the live
 * elapsed time while it's still rendering, or the frozen final total once it
 * succeeds. `null` before the job has actually started (still queued) and
 * for failed/partial jobs, where no duration is shown. */
export function computeRenderDurationMs(job: JobSnapshot | null, nowMs: number): number | null {
  if (!job || !job.started_at) return null;
  const startedAt = Date.parse(job.started_at);
  if (job.state === "SUCCEEDED" && job.finished_at) {
    return Math.max(0, Date.parse(job.finished_at) - startedAt);
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
