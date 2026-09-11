export interface TrimRange {
  startMs: number;
  endMs: number;
}

// `rangeStartMs` defaults to 0 (the plain trim case: the editable range is
// exactly the managed clip's own span) but can go negative once the
// trim/extend dialog has granted room before the clip's original start.
export function clampTrimRange(
  startMs: number,
  endMs: number,
  durationMs: number,
  activeBoundary: "start" | "end",
  rangeStartMs = 0,
): TrimRange {
  const floor = Math.floor(rangeStartMs);
  const ceiling = Math.max(floor + 1, Math.floor(durationMs));
  let start = Math.min(ceiling - 1, Math.max(floor, Math.floor(startMs)));
  let end = Math.min(ceiling, Math.max(floor + 1, Math.floor(endMs)));
  if (start >= end) {
    if (activeBoundary === "start") start = Math.max(floor, end - 1);
    else end = Math.min(ceiling, start + 1);
  }
  return { startMs: start, endMs: end };
}

// Text-entered timestamps (HH:MM:SS.mmm) have no negative notation, so this
// still only validates the non-negative portion of an extended range — a
// granted-but-not-yet-typed negative Start is only reachable via the
// timeline drag handle or the extend buttons, not by typing into the field.
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
