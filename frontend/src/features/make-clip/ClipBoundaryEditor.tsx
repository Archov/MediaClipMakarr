import AddRounded from "@mui/icons-material/AddRounded";
import AvTimerRounded from "@mui/icons-material/AvTimerRounded";
import RemoveRounded from "@mui/icons-material/RemoveRounded";
import RestartAltRounded from "@mui/icons-material/RestartAltRounded";
import { Alert, Box, Button, IconButton, Stack, TextField, Typography } from "@mui/material";
import { type ReactNode, useEffect, useRef } from "react";

import { formatTimestampMs, parseTimestampMs } from "../../timestamps";

const MAX_ADJUSTMENT_SECONDS = 99;

function clampAdjustmentSeconds(seconds: number): number {
  return Math.min(MAX_ADJUSTMENT_SECONDS, Math.max(-MAX_ADJUSTMENT_SECONDS, seconds));
}

function clampBoundaryMs(value: number, maximumMs: number | null | undefined): number {
  const nonNegative = Math.max(0, Math.floor(value));
  return maximumMs == null ? nonNegative : Math.min(nonNegative, maximumMs);
}

function adjustmentLabel(seconds: number): string {
  return seconds > 0 ? `+${seconds}` : seconds.toString();
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
  mediaDurationMs: number | null | undefined;
  adjustmentSeconds: number;
  onAdjustmentChange: (value: number) => void;
  onStartChange: (input: string, value: number | null) => void;
  onEndChange: (input: string, value: number | null) => void;
  children: ReactNode;
}

export function ClipBoundaryEditor({
  startInput,
  endInput,
  startMs,
  endMs,
  livePositionMs,
  mediaDurationMs,
  adjustmentSeconds,
  onAdjustmentChange,
  onStartChange,
  onEndChange,
  children,
}: ClipBoundaryEditorProps) {
  const inputRef = useRef<HTMLInputElement>(null);
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
      onAdjustmentChange(
        clampAdjustmentSeconds(adjustmentSeconds + (event.deltaY < 0 ? 1 : -1)),
      );
    };
    input.addEventListener("wheel", wheel, { passive: false });
    return () => input.removeEventListener("wheel", wheel);
  }, [adjustmentSeconds, onAdjustmentChange]);

  const setStart = (value: number | null) => {
    onStartChange(formatTimestampMs(value), value);
  };

  const setEnd = (value: number | null) => {
    onEndChange(formatTimestampMs(value), value);
  };

  const adjustStart = () => {
    if (startMs === null) return;
    setStart(clampBoundaryMs(startMs + adjustmentSeconds * 1_000, mediaDurationMs));
  };

  const adjustEnd = () => {
    const baseMs = endMs ?? startMs;
    if (baseMs === null) return;
    setEnd(clampBoundaryMs(baseMs + adjustmentSeconds * 1_000, mediaDurationMs));
  };

  return (
    <Stack spacing={2}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={2}
        justifyContent="space-between"
        alignItems={{ sm: "center" }}
      >
        <Box>
          <Typography variant="h6">Clip boundaries</Typography>
          <Typography color="text.secondary" sx={{ fontVariantNumeric: "tabular-nums" }}>
            Current position {formatMilliseconds(livePositionMs)}
          </Typography>
        </Box>
      </Stack>

      {children}

      <Stack direction="row" spacing={2} useFlexGap flexWrap="wrap" alignItems="flex-start">
        <Stack direction="row" spacing={1} alignItems="flex-start">
          <TextField
            label="Start"
            placeholder="00:00:00.000"
            value={startInput}
            error={Boolean(startParse.error)}
            helperText={startParse.error ?? "HH:MM:SS.mmm"}
            onChange={(event) => {
              const input = event.target.value;
              const parsed = parseTimestampMs(input);
              onStartChange(input, parsed.error ? null : parsed.value);
            }}
            sx={{ width: { xs: "15ch", sm: "16ch" } }}
          />
          <Button
            aria-label="Set Start to current position"
            startIcon={<AvTimerRounded />}
            variant="outlined"
            disabled={livePositionMs === null}
            onClick={() => setStart(livePositionMs)}
            sx={{ minHeight: 56 }}
          >
            Set
          </Button>
        </Stack>
        <Stack direction="row" spacing={1} alignItems="flex-start">
          <TextField
            label="End"
            placeholder="00:00:00.000"
            value={endInput}
            error={Boolean(endParse.error)}
            helperText={endParse.error ?? "HH:MM:SS.mmm"}
            onChange={(event) => {
              const input = event.target.value;
              const parsed = parseTimestampMs(input);
              onEndChange(input, parsed.error ? null : parsed.value);
            }}
            sx={{ width: { xs: "15ch", sm: "16ch" } }}
          />
          <Button
            aria-label="Set End to current position"
            startIcon={<AvTimerRounded />}
            variant="outlined"
            disabled={livePositionMs === null}
            onClick={() => setEnd(livePositionMs)}
            sx={{ minHeight: 56 }}
          >
            Set
          </Button>
        </Stack>
        <Button
          startIcon={<RestartAltRounded />}
          variant="outlined"
          onClick={() => {
            setStart(null);
            setEnd(null);
          }}
          sx={{ minHeight: 56 }}
        >
          Clear
        </Button>
      </Stack>

      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" alignItems="flex-start">
        <TextField
          inputRef={inputRef}
          type="number"
          label="Seconds"
          value={adjustmentSeconds}
          onChange={(event) => {
            const value = Number(event.target.value);
            onAdjustmentChange(
              Number.isFinite(value) ? clampAdjustmentSeconds(Math.trunc(value)) : 0,
            );
          }}
          slotProps={{
            htmlInput: { max: MAX_ADJUSTMENT_SECONDS, min: -MAX_ADJUSTMENT_SECONDS, step: 1 },
          }}
          sx={{ width: "12ch" }}
        />
        <Stack direction="row" spacing={0.5} sx={{ minHeight: 56 }}>
          <IconButton
            aria-label="Decrease seconds"
            onClick={() => onAdjustmentChange(clampAdjustmentSeconds(adjustmentSeconds - 1))}
            sx={{ border: 1, borderColor: "divider", borderRadius: 1, minHeight: 56 }}
          >
            <RemoveRounded />
          </IconButton>
          <IconButton
            aria-label="Increase seconds"
            onClick={() => onAdjustmentChange(clampAdjustmentSeconds(adjustmentSeconds + 1))}
            sx={{ border: 1, borderColor: "divider", borderRadius: 1, minHeight: 56 }}
          >
            <AddRounded />
          </IconButton>
        </Stack>
        <Button
          variant="outlined"
          disabled={startMs === null}
          onClick={adjustStart}
          sx={{ minHeight: 56, textTransform: "none", whiteSpace: "nowrap", width: 116 }}
        >
          Start {adjustmentLabel(adjustmentSeconds)}s
        </Button>
        <Button
          variant="outlined"
          disabled={endMs === null && startMs === null}
          onClick={adjustEnd}
          sx={{ minHeight: 56, textTransform: "none", whiteSpace: "nowrap", width: 116 }}
        >
          End {adjustmentLabel(adjustmentSeconds)}s
        </Button>
      </Stack>

      {startMs !== null && endMs !== null && endMs > startMs && (
        <Typography color="text.secondary" sx={{ fontVariantNumeric: "tabular-nums" }}>
          Selected duration {formatMilliseconds(endMs - startMs)}
        </Typography>
      )}
      {rangeError && <Alert severity="warning">{rangeError}</Alert>}
    </Stack>
  );
}
