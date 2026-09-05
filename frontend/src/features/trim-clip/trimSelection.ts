export interface TrimRange {
  startMs: number;
  endMs: number;
}

export function clampTrimRange(
  startMs: number,
  endMs: number,
  durationMs: number,
  activeBoundary: "start" | "end",
): TrimRange {
  const duration = Math.max(1, Math.floor(durationMs));
  let start = Math.min(duration - 1, Math.max(0, Math.floor(startMs)));
  let end = Math.min(duration, Math.max(1, Math.floor(endMs)));
  if (start >= end) {
    if (activeBoundary === "start") start = Math.max(0, end - 1);
    else end = Math.min(duration, start + 1);
  }
  return { startMs: start, endMs: end };
}

export function validateTrimValue(
  parsed: { value: number | null; error: string | null },
  boundary: "start" | "end",
  otherBoundaryMs: number,
  durationMs: number,
): { value: number | null; error: string | null } {
  if (parsed.error) return parsed;
  if (parsed.value === null) return { value: null, error: `${boundary === "start" ? "Start" : "End"} is required.` };
  if (parsed.value > durationMs) return { value: null, error: "Timestamp must be within the clip." };
  if (boundary === "start" && parsed.value >= otherBoundaryMs) {
    return { value: null, error: "Start must be earlier than End." };
  }
  if (boundary === "end" && parsed.value <= otherBoundaryMs) {
    return { value: null, error: "End must be later than Start." };
  }
  return { value: parsed.value, error: null };
}

export function shouldStopPreview(mediaTimeSeconds: number, endMs: number): boolean {
  return Math.round(mediaTimeSeconds * 1_000) >= endMs;
}
