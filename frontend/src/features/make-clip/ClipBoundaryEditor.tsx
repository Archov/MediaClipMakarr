import AddRounded from "@mui/icons-material/AddRounded";
import ChevronLeftRounded from "@mui/icons-material/ChevronLeftRounded";
import ChevronRightRounded from "@mui/icons-material/ChevronRightRounded";
import PlayForWorkRounded from "@mui/icons-material/PlayForWorkRounded";
import RemoveRounded from "@mui/icons-material/RemoveRounded";
import RestartAltRounded from "@mui/icons-material/RestartAltRounded";
import SkipNextRounded from "@mui/icons-material/SkipNextRounded";
import SkipPreviousRounded from "@mui/icons-material/SkipPreviousRounded";
import {
  Alert,
  Box,
  Button,
  IconButton,
  InputAdornment,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { sessionFrameUrl } from "../../api";
import { formatTimestampMs, parseTimestampMs } from "../../timestamps";
import { SessionFrameImage } from "./SessionFrameImage";

const MAX_ADJUSTMENT_VALUE = 99;
const MIN_ADJUSTMENT_VALUE = 1;

const pillSx = {
  display: "flex",
  alignItems: "center",
  border: 1,
  borderColor: "divider",
  borderRadius: 1,
  overflow: "hidden",
} as const;

type NudgeUnit = "minutes" | "seconds" | "centiseconds" | "milliseconds" | "frames";

// Cycle order matches the mock: coarse to fine, frames last.
const NUDGE_UNITS: NudgeUnit[] = ["minutes", "seconds", "centiseconds", "milliseconds", "frames"];

const NUDGE_UNIT_SUFFIX: Record<NudgeUnit, string> = {
  minutes: "m",
  seconds: "s",
  centiseconds: "cs",
  milliseconds: "ms",
  frames: "f",
};

const NUDGE_UNIT_MS_PER_STEP: Record<Exclude<NudgeUnit, "frames">, number> = {
  minutes: 60_000,
  seconds: 1_000,
  centiseconds: 10,
  milliseconds: 1,
};

function clampAdjustmentValue(value: number): number {
  return Math.min(MAX_ADJUSTMENT_VALUE, Math.max(MIN_ADJUSTMENT_VALUE, value));
}

function nudgeStepMs(unit: NudgeUnit, value: number, frameRate: number | null): number {
  if (unit === "frames") {
    return frameRate ? Math.round((value * 1_000) / frameRate) : 0;
  }
  return value * NUDGE_UNIT_MS_PER_STEP[unit];
}

function cycleNudgeUnit(current: NudgeUnit, direction: 1 | -1, framesAvailable: boolean): NudgeUnit {
  const units = framesAvailable ? NUDGE_UNITS : NUDGE_UNITS.filter((unit) => unit !== "frames");
  const index = units.indexOf(current);
  const nextIndex = ((index === -1 ? 0 : index) + direction + units.length) % units.length;
  return units[nextIndex];
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
  const inputRef = useRef<HTMLInputElement>(null);
  const [startPreview, setStartPreview] = useState<BoundaryPreview | null>(null);
  const [endPreview, setEndPreview] = useState<BoundaryPreview | null>(null);
  const [adjustmentValue, setAdjustmentValue] = useState(5);
  const [adjustmentUnit, setAdjustmentUnit] = useState<NudgeUnit>("seconds");
  const framesAvailable = Boolean(mediaFrameRate);
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
    const input = inputRef.current;
    if (!input) return;
    const wheel = (event: WheelEvent) => {
      if (event.deltaY === 0) return;
      event.preventDefault();
      setAdjustmentValue((value) => clampAdjustmentValue(value + (event.deltaY < 0 ? 1 : -1)));
    };
    input.addEventListener("wheel", wheel, { passive: false });
    return () => input.removeEventListener("wheel", wheel);
  }, []);

  useEffect(() => {
    if (adjustmentUnit === "frames" && !framesAvailable) {
      setAdjustmentUnit("seconds");
    }
  }, [adjustmentUnit, framesAvailable]);

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
          <Stack direction="row" spacing={1} alignItems="flex-start">
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
                  <PlayForWorkRounded />
                </IconButton>
              </span>
            </Tooltip>
          </Stack>
        </Stack>
        <Stack spacing={1} sx={{ width: 260, maxWidth: "100%" }}>
          <PreviewSlot label="End" preview={endPreview} />
          <Stack direction="row" spacing={1} alignItems="flex-start">
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
                  <PlayForWorkRounded />
                </IconButton>
              </span>
            </Tooltip>
          </Stack>
        </Stack>
      </Stack>

      <Stack direction="row" spacing={2} useFlexGap flexWrap="wrap" alignItems="center" justifyContent="center">
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="caption" sx={{ fontWeight: 600, color: "text.secondary", letterSpacing: "0.04em" }}>
            NUDGE BY
          </Typography>
          <Stack direction="row" alignItems="center" sx={pillSx}>
            <Tooltip title="Coarser unit">
              <span>
                <IconButton
                  aria-label="Switch to coarser nudge unit"
                  onClick={() => setAdjustmentUnit((unit) => cycleNudgeUnit(unit, -1, framesAvailable))}
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
            <TextField
              inputRef={inputRef}
              type="text"
              variant="standard"
              value={adjustmentValue}
              onChange={(event) => {
                const value = Number(event.target.value.replace(/[^0-9]/g, ""));
                setAdjustmentValue(
                  Number.isFinite(value) ? clampAdjustmentValue(Math.trunc(value)) : MIN_ADJUSTMENT_VALUE,
                );
              }}
              slotProps={{
                htmlInput: {
                  inputMode: "numeric",
                  pattern: "[0-9]*",
                  style: { textAlign: "center", width: "2.5ch" },
                },
                input: {
                  disableUnderline: true,
                  endAdornment: (
                    <InputAdornment position="end">{NUDGE_UNIT_SUFFIX[adjustmentUnit]}</InputAdornment>
                  ),
                },
              }}
              sx={{ px: 1 }}
            />
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
                  onClick={() => setAdjustmentUnit((unit) => cycleNudgeUnit(unit, 1, framesAvailable))}
                  sx={{ borderRadius: 0, borderLeft: 1, borderColor: "divider" }}
                >
                  <SkipNextRounded fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Stack>
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
          <Typography variant="body2" sx={{ px: 1.5, fontWeight: 600, whiteSpace: "nowrap" }}>
            Start
          </Typography>
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
          <Typography variant="body2" sx={{ px: 1.5, fontWeight: 600, whiteSpace: "nowrap" }}>
            End
          </Typography>
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
