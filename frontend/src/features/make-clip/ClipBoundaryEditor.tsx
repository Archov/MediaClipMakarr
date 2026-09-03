import AddLocationAltRounded from "@mui/icons-material/AddLocationAltRounded";
import AddRounded from "@mui/icons-material/AddRounded";
import ChevronLeftRounded from "@mui/icons-material/ChevronLeftRounded";
import ChevronRightRounded from "@mui/icons-material/ChevronRightRounded";
import RemoveRounded from "@mui/icons-material/RemoveRounded";
import RestartAltRounded from "@mui/icons-material/RestartAltRounded";
import SkipNextRounded from "@mui/icons-material/SkipNextRounded";
import SkipPreviousRounded from "@mui/icons-material/SkipPreviousRounded";
import SystemUpdateAltRounded from "@mui/icons-material/SystemUpdateAltRounded";
import {
  Alert,
  Box,
  Button,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { sessionFrameUrl } from "../../api";
import { formatTimestampMs, parseTimestampMs } from "../../timestamps";
import { SessionFrameImage } from "./SessionFrameImage";

const MIN_ADJUSTMENT_VALUE = 1;
const MAX_ADJUSTMENT_VALUE = 99;

const pillSx = {
  display: "flex",
  alignItems: "center",
  border: 1,
  borderColor: "divider",
  borderRadius: 1,
  overflow: "hidden",
} as const;

type NudgeUnit = "minutes" | "seconds" | "tenths" | "hundredths" | "frames";

// Cycle order: coarsest to finest, frames last. Cycling stops at either end
// rather than wrapping.
const NUDGE_UNITS: NudgeUnit[] = ["minutes", "seconds", "tenths", "hundredths", "frames"];

// Static decimal-place prefix for the two sub-second units, e.g. tenths reads
// "0." + digit + "s" (0.5s), hundredths reads "0.0" + digit + "s" (0.05s).
const NUDGE_UNIT_PREFIX: Partial<Record<NudgeUnit, string>> = {
  tenths: "0.",
  hundredths: "0.0",
};

const NUDGE_UNIT_SUFFIX: Record<NudgeUnit, string> = {
  minutes: "m",
  seconds: "s",
  tenths: "s",
  hundredths: "s",
  frames: "frame",
};

const NUDGE_UNIT_MS_PER_STEP: Record<"minutes" | "seconds", number> = {
  minutes: 60_000,
  seconds: 1_000,
};

function nudgeUnitSuffix(unit: NudgeUnit, value: number): string {
  return unit === "frames" && value > 1 ? "frames" : NUDGE_UNIT_SUFFIX[unit];
}

function clampAdjustmentValue(value: number): number {
  return Math.min(MAX_ADJUSTMENT_VALUE, Math.max(MIN_ADJUSTMENT_VALUE, value));
}

function nudgeStepMs(unit: NudgeUnit, value: number, frameRate: number | null): number {
  if (unit === "frames") {
    return frameRate ? Math.round((value * 1_000) / frameRate) : 0;
  }
  const prefix = NUDGE_UNIT_PREFIX[unit];
  if (prefix) {
    // Parse the literal displayed decimal (e.g. prefix "0.0" + value "23" ->
    // 0.023) so the applied delta always matches what's on screen, regardless
    // of how many digits are currently entered.
    return Math.round(Number(`${prefix}${value}`) * 1_000);
  }
  return value * NUDGE_UNIT_MS_PER_STEP[unit as "minutes" | "seconds"];
}

function availableNudgeUnits(framesAvailable: boolean): NudgeUnit[] {
  return framesAvailable ? NUDGE_UNITS : NUDGE_UNITS.filter((unit) => unit !== "frames");
}

// Steps toward finer (direction 1) or coarser (direction -1) units, holding at
// either end instead of wrapping around.
function stepNudgeUnit(current: NudgeUnit, direction: 1 | -1, framesAvailable: boolean): NudgeUnit {
  const units = availableNudgeUnits(framesAvailable);
  const index = units.indexOf(current);
  const safeIndex = index === -1 ? 0 : index;
  const nextIndex = safeIndex + direction;
  return nextIndex < 0 || nextIndex >= units.length ? units[safeIndex] : units[nextIndex];
}

function clampBoundaryMs(value: number, maximumMs: number | null | undefined): number {
  const nonNegative = Math.max(0, Math.floor(value));
  return maximumMs == null ? nonNegative : Math.min(nonNegative, maximumMs);
}

function formatMilliseconds(value: number | null): string {
  return formatTimestampMs(value) || "--:--";
}

interface ClipBoundaryEditorProps {
  startInput: string;
  endInput: string;
  startMs: number | null;
  endMs: number | null;
  livePositionMs: number | null;
  sessionIdentity: string;
  mediaIdentity: string;
  mediaDurationMs: number | null | undefined;
  mediaFrameRate: number | null;
  onStartChange: (input: string, value: number | null) => void;
  onEndChange: (input: string, value: number | null) => void;
  children: ReactNode;
}

interface BoundaryPreview {
  sessionIdentity: string;
  mediaIdentity: string;
  positionMs: number;
  version: number;
}

function PreviewSlot({ label, preview }: { label: "Start" | "End"; preview: BoundaryPreview | null }) {
  return (
    <Stack spacing={0.75}>
      <Typography variant="body2" color="text.secondary">
        {label} preview{preview ? ` · ${formatMilliseconds(preview.positionMs)}` : ""}
      </Typography>
      {preview ? (
        <SessionFrameImage
          source={sessionFrameUrl(
            preview.sessionIdentity,
            preview.mediaIdentity,
            preview.positionMs,
            preview.version,
          )}
          alt={`${label} frame at ${formatMilliseconds(preview.positionMs)}`}
          width="100%"
        />
      ) : (
        <Box
          sx={{
            width: "100%",
            aspectRatio: "16 / 9",
            display: "grid",
            placeItems: "center",
            border: 1,
            borderStyle: "dashed",
            borderColor: "divider",
            borderRadius: 1,
            bgcolor: "action.hover",
          }}
        >
          <Typography variant="caption" color="text.secondary">No preview captured</Typography>
        </Box>
      )}
    </Stack>
  );
}

function exportFrameUrl(preview: BoundaryPreview | null): string | undefined {
  return preview
    ? sessionFrameUrl(preview.sessionIdentity, preview.mediaIdentity, preview.positionMs, preview.version, true)
    : undefined;
}

export function ClipBoundaryEditor({
  startInput,
  endInput,
  startMs,
  endMs,
  livePositionMs,
  sessionIdentity,
  mediaIdentity,
  mediaDurationMs,
  mediaFrameRate,
  onStartChange,
  onEndChange,
  children,
}: ClipBoundaryEditorProps) {
  const nudgeValueBoxRef = useRef<HTMLDivElement>(null);
  const [startPreview, setStartPreview] = useState<BoundaryPreview | null>(null);
  const [endPreview, setEndPreview] = useState<BoundaryPreview | null>(null);
  const [adjustmentValue, setAdjustmentValue] = useState(5);
  const [adjustmentUnit, setAdjustmentUnit] = useState<NudgeUnit>("seconds");
  const framesAvailable = Boolean(mediaFrameRate);
  const availableUnits = availableNudgeUnits(framesAvailable);
  const adjustmentUnitIndex = availableUnits.indexOf(adjustmentUnit);
  const isCoarsestUnit = adjustmentUnitIndex <= 0;
  const isFinestUnit = adjustmentUnitIndex === availableUnits.length - 1;
  const startParse = parseTimestampMs(startInput);
  const endParse = parseTimestampMs(endInput);
  const rangeError =
    startParse.error ??
    endParse.error ??
    (startMs === null ? "Capture Start before creating a clip." : null) ??
    (endMs === null ? "Capture End before creating a clip." : null) ??
    (startMs !== null && endMs !== null && endMs <= startMs
      ? "End must be later than Start."
      : null) ??
    (endMs !== null && mediaDurationMs != null && endMs > mediaDurationMs
      ? "End must be within the selected media duration."
      : null);

  useEffect(() => {
    const box = nudgeValueBoxRef.current;
    if (!box) return;
    const wheel = (event: WheelEvent) => {
      if (event.deltaY === 0) return;
      event.preventDefault();
      setAdjustmentValue((value) => clampAdjustmentValue(value + (event.deltaY < 0 ? 1 : -1)));
    };
    box.addEventListener("wheel", wheel, { passive: false });
    return () => box.removeEventListener("wheel", wheel);
  }, []);

  useEffect(() => {
    if (adjustmentUnit === "frames" && !framesAvailable) {
      setAdjustmentUnit("seconds");
    }
  }, [adjustmentUnit, framesAvailable]);

  const cycleAdjustmentUnit = (direction: 1 | -1) => {
    const next = stepNudgeUnit(adjustmentUnit, direction, framesAvailable);
    setAdjustmentUnit(next);
  };

  useEffect(() => {
    setStartPreview((current) => {
      if (!current) return null;
      return startMs === current.positionMs
        && current.sessionIdentity === sessionIdentity
        && current.mediaIdentity === mediaIdentity
        ? current
        : null;
    });
    setEndPreview((current) => {
      if (!current) return null;
      return endMs === current.positionMs
        && current.sessionIdentity === sessionIdentity
        && current.mediaIdentity === mediaIdentity
        ? current
        : null;
    });
  }, [endMs, mediaIdentity, sessionIdentity, startMs]);

  // A freshly selected session/media can arrive with Start already pre-filled
  // by the parent (the current playback position), bypassing the nudge/set-to-
  // current/blur paths below that normally trigger a preview fetch. Fetch one
  // here whenever the selection itself changes, for whichever boundary is set.
  useEffect(() => {
    commitStartPreview(startMs);
    commitEndPreview(endMs);
    // Only re-run when the selection changes, not on every startMs/endMs edit -
    // those are already covered by the explicit commit calls below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionIdentity, mediaIdentity]);

  const commitStartPreview = (positionMs: number | null) => {
    if (positionMs === null) {
      setStartPreview(null);
      return;
    }
    setStartPreview((current) => ({
      sessionIdentity,
      mediaIdentity,
      positionMs,
      version: (current?.version ?? 0) + 1,
    }));
  };

  const commitEndPreview = (positionMs: number | null) => {
    if (positionMs === null) {
      setEndPreview(null);
      return;
    }
    setEndPreview((current) => ({
      sessionIdentity,
      mediaIdentity,
      positionMs,
      version: (current?.version ?? 0) + 1,
    }));
  };

  const setStart = (value: number | null) => {
    onStartChange(formatTimestampMs(value), value);
    commitStartPreview(value);
  };

  const setEnd = (value: number | null) => {
    onEndChange(formatTimestampMs(value), value);
    commitEndPreview(value);
  };

  const adjustStart = (direction: 1 | -1) => {
    if (startMs === null) return;
    const stepMs = nudgeStepMs(adjustmentUnit, adjustmentValue, mediaFrameRate);
    setStart(clampBoundaryMs(startMs + direction * stepMs, mediaDurationMs));
  };

  const adjustEnd = (direction: 1 | -1) => {
    const baseMs = endMs ?? startMs;
    if (baseMs === null) return;
    const stepMs = nudgeStepMs(adjustmentUnit, adjustmentValue, mediaFrameRate);
    setEnd(clampBoundaryMs(baseMs + direction * stepMs, mediaDurationMs));
  };

  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={2} useFlexGap flexWrap="wrap" alignItems="flex-start" justifyContent="center">
        <Stack spacing={1} sx={{ width: 260, maxWidth: "100%" }}>
          <PreviewSlot label="Start" preview={startPreview} />
          <Stack direction="row" spacing={1} alignItems="flex-start" justifyContent="center">
            <TextField
              label="Start"
              placeholder="00:00:00.000"
              value={startInput}
              error={Boolean(startParse.error)}
              helperText={startParse.error}
              onChange={(event) => {
                const input = event.target.value;
                const parsed = parseTimestampMs(input);
                setStartPreview(null);
                onStartChange(input, parsed.error ? null : parsed.value);
              }}
              onBlur={() => {
                if (!startParse.error) commitStartPreview(startParse.value);
              }}
              sx={{ width: { xs: "15ch", sm: "16ch" } }}
            />
            <Tooltip title="Set to current stream time">
              <span>
                <IconButton
                  aria-label="Set Start to current stream time"
                  disabled={livePositionMs === null}
                  onClick={() => setStart(livePositionMs)}
                  sx={{ border: 1, borderColor: "divider", borderRadius: 1, minHeight: 56 }}
                >
                  <AddLocationAltRounded />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="Export frame">
              <span>
                <IconButton
                  aria-label="Export Start frame"
                  component="a"
                  href={exportFrameUrl(startPreview)}
                  download=""
                  disabled={!startPreview}
                  sx={{ border: 1, borderColor: "divider", borderRadius: 1, minHeight: 56 }}
                >
                  <SystemUpdateAltRounded />
                </IconButton>
              </span>
            </Tooltip>
          </Stack>
        </Stack>
        <Stack spacing={1} sx={{ width: 260, maxWidth: "100%" }}>
          <PreviewSlot label="End" preview={endPreview} />
          <Stack direction="row" spacing={1} alignItems="flex-start" justifyContent="center">
            <TextField
              label="End"
              placeholder="00:00:00.000"
              value={endInput}
              error={Boolean(endParse.error)}
              helperText={endParse.error}
              onChange={(event) => {
                const input = event.target.value;
                const parsed = parseTimestampMs(input);
                setEndPreview(null);
                onEndChange(input, parsed.error ? null : parsed.value);
              }}
              onBlur={() => {
                if (!endParse.error) commitEndPreview(endParse.value);
              }}
              sx={{ width: { xs: "15ch", sm: "16ch" } }}
            />
            <Tooltip title="Set to current stream time">
              <span>
                <IconButton
                  aria-label="Set End to current stream time"
                  disabled={livePositionMs === null}
                  onClick={() => setEnd(livePositionMs)}
                  sx={{ border: 1, borderColor: "divider", borderRadius: 1, minHeight: 56 }}
                >
                  <AddLocationAltRounded />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="Export frame">
              <span>
                <IconButton
                  aria-label="Export End frame"
                  component="a"
                  href={exportFrameUrl(endPreview)}
                  download=""
                  disabled={!endPreview}
                  sx={{ border: 1, borderColor: "divider", borderRadius: 1, minHeight: 56 }}
                >
                  <SystemUpdateAltRounded />
                </IconButton>
              </span>
            </Tooltip>
          </Stack>
        </Stack>
      </Stack>

      <Stack spacing={1} alignItems="center">
        <Typography variant="caption" sx={{ fontWeight: 600, color: "text.secondary", letterSpacing: "0.04em" }}>
          NUDGE
        </Typography>
        <Stack direction="row" spacing={2} useFlexGap flexWrap="wrap" alignItems="center" justifyContent="center">
          <Stack direction="row" alignItems="center" sx={pillSx}>
            <Tooltip title="Coarser unit">
              <span>
                <IconButton
                  aria-label="Switch to coarser nudge unit"
                  disabled={isCoarsestUnit}
                  onClick={() => cycleAdjustmentUnit(-1)}
                  sx={{ borderRadius: 0, borderRight: 1, borderColor: "divider" }}
                >
                  <SkipPreviousRounded fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <IconButton
              aria-label="Decrease nudge amount"
              onClick={() => setAdjustmentValue((value) => clampAdjustmentValue(value - 1))}
              sx={{ borderRadius: 0, borderRight: 1, borderColor: "divider" }}
            >
              <ChevronLeftRounded fontSize="small" />
            </IconButton>
            <Tooltip title="Unit count to nudge">
              <Box
                ref={nudgeValueBoxRef}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  // Tight when a decimal prefix is shown, so "0.023s" reads as
                  // one number; a small gap between the digits and a bare unit
                  // label (e.g. "20 frame") otherwise.
                  gap: NUDGE_UNIT_PREFIX[adjustmentUnit] ? 0 : "5px",
                  minWidth: 108,
                  alignSelf: "stretch",
                  px: 1.5,
                  cursor: "default",
                  userSelect: "none",
                }}
              >
                {NUDGE_UNIT_PREFIX[adjustmentUnit] && (
                  <Typography
                    component="span"
                    sx={{ fontSize: "1rem", fontWeight: 600, color: "text.primary" }}
                  >
                    {NUDGE_UNIT_PREFIX[adjustmentUnit]}
                  </Typography>
                )}
                <Typography component="span" sx={{ fontSize: "1rem", fontWeight: 600, color: "text.primary" }}>
                  {adjustmentValue}
                </Typography>
                <Typography
                  component="span"
                  sx={{ fontSize: "1rem", fontWeight: 600, color: "text.primary", whiteSpace: "nowrap" }}
                >
                  {nudgeUnitSuffix(adjustmentUnit, adjustmentValue)}
                </Typography>
              </Box>
            </Tooltip>
            <IconButton
              aria-label="Increase nudge amount"
              onClick={() => setAdjustmentValue((value) => clampAdjustmentValue(value + 1))}
              sx={{ borderRadius: 0, borderLeft: 1, borderColor: "divider" }}
            >
              <ChevronRightRounded fontSize="small" />
            </IconButton>
            <Tooltip title="Finer unit">
              <span>
                <IconButton
                  aria-label="Switch to finer nudge unit"
                  disabled={isFinestUnit}
                  onClick={() => cycleAdjustmentUnit(1)}
                  sx={{ borderRadius: 0, borderLeft: 1, borderColor: "divider" }}
                >
                  <SkipNextRounded fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Stack>

          <Stack direction="row" alignItems="center" sx={pillSx}>
            <IconButton
              aria-label="Nudge start earlier"
              disabled={startMs === null}
              onClick={() => adjustStart(-1)}
              sx={{ borderRadius: 0, borderRight: 1, borderColor: "divider" }}
            >
              <RemoveRounded fontSize="small" />
            </IconButton>
            <Tooltip title="Add or Subtract time from the current start timestamp">
              <Typography
                variant="body2"
                sx={{ px: 1.5, fontWeight: 600, whiteSpace: "nowrap", cursor: "default", userSelect: "none" }}
              >
                Start
              </Typography>
            </Tooltip>
            <IconButton
              aria-label="Nudge start later"
              disabled={startMs === null}
              onClick={() => adjustStart(1)}
              sx={{ borderRadius: 0, borderLeft: 1, borderColor: "divider" }}
            >
              <AddRounded fontSize="small" />
            </IconButton>
          </Stack>

          <Stack direction="row" alignItems="center" sx={pillSx}>
            <IconButton
              aria-label="Nudge end earlier"
              disabled={endMs === null && startMs === null}
              onClick={() => adjustEnd(-1)}
              sx={{ borderRadius: 0, borderRight: 1, borderColor: "divider" }}
            >
              <RemoveRounded fontSize="small" />
            </IconButton>
            <Tooltip title="Add or Subtract time from the current end timestamp">
              <Typography
                variant="body2"
                sx={{ px: 1.5, fontWeight: 600, whiteSpace: "nowrap", cursor: "default", userSelect: "none" }}
              >
                End
              </Typography>
            </Tooltip>
            <IconButton
              aria-label="Nudge end later"
              disabled={endMs === null && startMs === null}
              onClick={() => adjustEnd(1)}
              sx={{ borderRadius: 0, borderLeft: 1, borderColor: "divider" }}
            >
              <AddRounded fontSize="small" />
            </IconButton>
          </Stack>

          <Button
            startIcon={<RestartAltRounded />}
            variant="outlined"
            onClick={() => {
              setStartPreview(null);
              setEndPreview(null);
              setStart(null);
              setEnd(null);
            }}
          >
            Clear
          </Button>
        </Stack>
      </Stack>

      {startMs !== null && endMs !== null && endMs > startMs && (
        <Typography color="text.secondary" sx={{ fontVariantNumeric: "tabular-nums", textAlign: "center" }}>
          Selected duration {formatMilliseconds(endMs - startMs)}
        </Typography>
      )}

      {children}

      {rangeError && <Alert severity="warning">{rangeError}</Alert>}
    </Stack>
  );
}
