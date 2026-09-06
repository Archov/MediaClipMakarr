export interface TimestampParseResult {
  value: number | null;
  error: string | null;
}

/** Parse an API timestamp as UTC. The backend serializes naive-but-UTC
 * datetimes (no trailing "Z" or offset) — left to plain `Date.parse`, an
 * offset-less ISO string is interpreted as local time instead, silently
 * skewing any comparison against `Date.now()` by the browser's UTC offset
 * (e.g. a "just started" job reading as hours old, or hours in the future,
 * clamped away to look permanently frozen at zero). */
export function parseUtcMs(value: string): number {
  const hasTimezone = /[zZ]|[+-]\d\d:?\d\d$/.test(value);
  return Date.parse(hasTimezone ? value : `${value}Z`);
}

const TIMESTAMP_PATTERN = /^(\d+):([0-5]\d):([0-5]\d)(?:\.(\d{1,3}))?$/;

export function formatTimestampMs(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "";
  const total = Math.max(0, Math.floor(value));
  const hours = Math.floor(total / 3_600_000);
  const minutes = Math.floor((total % 3_600_000) / 60_000);
  const seconds = Math.floor((total % 60_000) / 1_000);
  const milliseconds = total % 1_000;
  return `${hours.toString().padStart(2, "0")}:${minutes
    .toString()
    .padStart(2, "0")}:${seconds.toString().padStart(2, "0")}.${milliseconds
    .toString()
    .padStart(3, "0")}`;
}

export function parseTimestampMs(value: string): TimestampParseResult {
  const trimmed = value.trim();
  if (!trimmed) return { value: null, error: null };

  const match = TIMESTAMP_PATTERN.exec(trimmed);
  if (!match) {
    return {
      value: null,
      error: "Use HH:MM:SS.mmm.",
    };
  }

  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  const seconds = Number(match[3]);
  const milliseconds = Number((match[4] ?? "0").padEnd(3, "0"));
  return {
    value: hours * 3_600_000 + minutes * 60_000 + seconds * 1_000 + milliseconds,
    error: null,
  };
}
