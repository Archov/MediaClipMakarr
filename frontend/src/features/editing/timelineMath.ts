export interface TimelineRange {
  startMs: number;
  endMs: number;
}

export interface TimelineTick {
  valueMs: number;
  major: boolean;
}

const NICE_INTERVAL_MULTIPLIERS = [1, 2, 5, 10] as const;

export function isValidTimelineRange(range: TimelineRange): boolean {
  return Number.isFinite(range.startMs) && Number.isFinite(range.endMs) && range.endMs > range.startMs;
}

export function effectiveEditableRange(
  viewportRange: TimelineRange,
  editableRange?: TimelineRange,
): TimelineRange {
  return editableRange && isValidTimelineRange(editableRange) ? editableRange : viewportRange;
}

export function clampTimelineValue(valueMs: number, range: TimelineRange): number {
  return Math.min(range.endMs, Math.max(range.startMs, Math.round(valueMs)));
}

export function timeToTimelinePercent(valueMs: number, viewportRange: TimelineRange): number {
  if (!isValidTimelineRange(viewportRange)) return 0;
  return ((valueMs - viewportRange.startMs) / (viewportRange.endMs - viewportRange.startMs)) * 100;
}

export function pointerPositionToTime(
  clientX: number,
  trackLeft: number,
  trackWidth: number,
  viewportRange: TimelineRange,
): number {
  if (trackWidth <= 0 || !isValidTimelineRange(viewportRange)) return viewportRange.startMs;
  const fraction = Math.min(1, Math.max(0, (clientX - trackLeft) / trackWidth));
  return Math.round(viewportRange.startMs + fraction * (viewportRange.endMs - viewportRange.startMs));
}

export function visibleTimelineIntersection(
  range: TimelineRange | undefined,
  viewportRange: TimelineRange,
): TimelineRange | null {
  if (!range || !isValidTimelineRange(range) || !isValidTimelineRange(viewportRange)) return null;
  const startMs = Math.max(range.startMs, viewportRange.startMs);
  const endMs = Math.min(range.endMs, viewportRange.endMs);
  return endMs > startMs ? { startMs, endMs } : null;
}

export function clampTimelineSelection(
  selectionRange: TimelineRange,
  editableRange: TimelineRange,
  activeBoundary: "start" | "end",
): TimelineRange {
  const minimumGapMs = 1;
  let startMs = Math.min(
    editableRange.endMs - minimumGapMs,
    Math.max(editableRange.startMs, Math.round(selectionRange.startMs)),
  );
  let endMs = Math.min(
    editableRange.endMs,
    Math.max(editableRange.startMs + minimumGapMs, Math.round(selectionRange.endMs)),
  );

  if (startMs >= endMs) {
    if (activeBoundary === "start") startMs = Math.max(editableRange.startMs, endMs - minimumGapMs);
    else endMs = Math.min(editableRange.endMs, startMs + minimumGapMs);
  }
  return { startMs, endMs };
}

export function canShiftTimelineBoundary(
  selectionRange: TimelineRange,
  editableRange: TimelineRange,
  boundary: "start" | "end",
  deltaMs: number,
): boolean {
  if (!Number.isFinite(deltaMs) || deltaMs === 0) return false;
  if (boundary === "start") {
    const candidate = selectionRange.startMs + deltaMs;
    return candidate >= editableRange.startMs && candidate < selectionRange.endMs;
  }
  const candidate = selectionRange.endMs + deltaMs;
  return candidate <= editableRange.endMs && candidate > selectionRange.startMs;
}

export function shiftTimelineBoundary(
  selectionRange: TimelineRange,
  editableRange: TimelineRange,
  boundary: "start" | "end",
  deltaMs: number,
): TimelineRange {
  if (!canShiftTimelineBoundary(selectionRange, editableRange, boundary, deltaMs)) return selectionRange;
  return boundary === "start"
    ? { ...selectionRange, startMs: selectionRange.startMs + deltaMs }
    : { ...selectionRange, endMs: selectionRange.endMs + deltaMs };
}

function niceMajorInterval(rawIntervalMs: number): number {
  const exponent = 10 ** Math.floor(Math.log10(Math.max(1, rawIntervalMs)));
  const normalized = rawIntervalMs / exponent;
  const multiplier = NICE_INTERVAL_MULTIPLIERS.find((candidate) => candidate >= normalized) ?? 10;
  return multiplier * exponent;
}

export function createTimelineTicks(viewportRange: TimelineRange, widthPx: number): TimelineTick[] {
  if (!isValidTimelineRange(viewportRange)) return [];
  const durationMs = viewportRange.endMs - viewportRange.startMs;
  const targetMajorCount = Math.max(2, Math.floor(Math.max(1, widthPx) / 110));
  const majorIntervalMs = niceMajorInterval(durationMs / targetMajorCount);
  const minorIntervalMs = Math.max(1, majorIntervalMs / 5);
  const firstTick = Math.ceil(viewportRange.startMs / minorIntervalMs) * minorIntervalMs;
  const ticks: TimelineTick[] = [];

  for (let valueMs = firstTick; valueMs <= viewportRange.endMs; valueMs += minorIntervalMs) {
    const roundedValue = Math.round(valueMs);
    const majorRemainder = Math.abs(valueMs / majorIntervalMs - Math.round(valueMs / majorIntervalMs));
    ticks.push({ valueMs: roundedValue, major: majorRemainder < 1e-7 });
    if (ticks.length > 250) break;
  }
  return ticks;
}

export function formatTimelineRulerTime(valueMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(valueMs / 1_000));
  const hours = Math.floor(totalSeconds / 3_600);
  const minutes = Math.floor((totalSeconds % 3_600) / 60);
  const seconds = totalSeconds % 60;
  return hours > 0
    ? `${hours}:${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`
    : `${minutes}:${seconds.toString().padStart(2, "0")}`;
}
