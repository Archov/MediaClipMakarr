export type NudgeUnit = "minutes" | "seconds" | "tenths" | "hundredths" | "frames";

export const NUDGE_UNITS: NudgeUnit[] = ["minutes", "seconds", "tenths", "hundredths", "frames"];

export const NUDGE_UNIT_PREFIX: Partial<Record<NudgeUnit, string>> = {
  tenths: "0.",
  hundredths: "0.0",
};

export const NUDGE_UNIT_SUFFIX: Record<NudgeUnit, string> = {
  minutes: "m",
  seconds: "s",
  tenths: "s",
  hundredths: "s",
  frames: "frame",
};

const MIN_ADJUSTMENT_VALUE = 1;
const MAX_ADJUSTMENT_VALUE = 99;

export function nudgeUnitSuffix(unit: NudgeUnit, value: number): string {
  return unit === "frames" && value > 1 ? "frames" : NUDGE_UNIT_SUFFIX[unit];
}

export function clampAdjustmentValue(value: number): number {
  return Math.min(MAX_ADJUSTMENT_VALUE, Math.max(MIN_ADJUSTMENT_VALUE, value));
}

export function nudgeStepMs(unit: NudgeUnit, value: number, frameRate: number | null): number {
  if (unit === "frames") {
    // Frame-rate nudging is nominal-duration navigation. For VFR media, this
    // does not claim that the resulting millisecond lands on a decoded frame.
    return frameRate ? Math.round((value * 1_000) / frameRate) : 0;
  }
  const prefix = NUDGE_UNIT_PREFIX[unit];
  if (prefix) return Math.round(Number(`${prefix}${value}`) * 1_000);
  return value * (unit === "minutes" ? 60_000 : 1_000);
}

export function availableNudgeUnits(framesAvailable: boolean): NudgeUnit[] {
  return framesAvailable ? NUDGE_UNITS : NUDGE_UNITS.filter((unit) => unit !== "frames");
}

export function stepNudgeUnit(
  current: NudgeUnit,
  direction: 1 | -1,
  framesAvailable: boolean,
): NudgeUnit {
  const units = availableNudgeUnits(framesAvailable);
  const index = units.indexOf(current);
  const safeIndex = index === -1 ? units.indexOf("seconds") : index;
  const nextIndex = safeIndex + direction;
  return nextIndex < 0 || nextIndex >= units.length ? units[safeIndex] : units[nextIndex];
}

export function clampBoundaryMs(value: number, maximumMs: number | null | undefined): number {
  const nonNegative = Math.max(0, Math.floor(value));
  return maximumMs == null ? nonNegative : Math.min(nonNegative, maximumMs);
}
