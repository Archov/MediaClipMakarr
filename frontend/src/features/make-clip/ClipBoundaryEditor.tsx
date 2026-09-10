import AddLocationAltRounded from "@mui/icons-material/AddLocationAltRounded";
import RestartAltRounded from "@mui/icons-material/RestartAltRounded";
import SystemUpdateAltRounded from "@mui/icons-material/SystemUpdateAltRounded";
import {
  Alert,
  Box,
  IconButton,
  Stack,
  Tab,
  Tabs,
  Tooltip,
  Typography,
} from "@mui/material";
import { type ReactNode, useEffect, useState } from "react";

import { sessionFrameUrl } from "../../api";
import type { CropBox } from "../../types";
import { formatTimestampMs, parseTimestampMs } from "../../timestamps";
import { FrameNudgeButton, TimestampField } from "../editing/BoundaryFieldControls";
import {
  clampAdjustmentValue,
  clampBoundaryMs,
  nudgeStepMs,
  type NudgeUnit,
} from "./boundaryNudges";
import { NudgeAmountControl } from "./NudgeAmountControl";
import { SessionFrameImage } from "./SessionFrameImage";

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
  crop?: CropBox | null;
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

function BoundaryPreviewFrame({
  boundary,
  preview,
  crop,
  exportUrl,
}: {
  boundary: "Start" | "End";
  preview: BoundaryPreview | null;
  crop?: CropBox | null;
  exportUrl?: string;
}) {
  return (
    <Stack spacing={0.75}>
      {preview ? (
        <Box sx={{ position: "relative" }}>
          <SessionFrameImage
            source={sessionFrameUrl(
              preview.sessionIdentity,
              preview.mediaIdentity,
              preview.positionMs,
              preview.version,
              false,
              crop,
            )}
            alt={`${boundary} frame at ${formatMilliseconds(preview.positionMs)}`}
            width="100%"
          />
          <Tooltip title="Export frame">
            <IconButton
              aria-label={`Export ${boundary} frame`}
              component="a"
              href={exportUrl}
              download=""
              sx={{
                position: "absolute",
                right: 8,
                bottom: 8,
                color: "common.white",
                bgcolor: "rgba(0, 0, 0, 0.5)",
                "&:hover": { bgcolor: "rgba(0, 0, 0, 0.7)" },
              }}
            >
              <SystemUpdateAltRounded fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>
      ) : (
        <Box
          sx={{
            width: "100%",
            // No frame has been captured here yet, so there's nothing real
            // to size from — 16:9 is just this placeholder's own shape, not
            // a claim about the video.
            aspectRatio: crop ? `${crop.width} / ${crop.height}` : "16 / 9",
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
      <Typography variant="caption" color="text.secondary" sx={{ textAlign: "center" }}>
        {preview ? formatMilliseconds(preview.positionMs) : " "}
      </Typography>
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
  crop,
  onStartChange,
  onEndChange,
  children,
}: ClipBoundaryEditorProps) {
  const [startPreview, setStartPreview] = useState<BoundaryPreview | null>(null);
  const [endPreview, setEndPreview] = useState<BoundaryPreview | null>(null);
  const [activeBoundary, setActiveBoundary] = useState<"start" | "end">("start");
  const framesAvailable = Boolean(mediaFrameRate);
  const [nudgeValue, setNudgeValue] = useState(() => clampAdjustmentValue(5));
  const [nudgeUnit, setNudgeUnit] = useState<NudgeUnit>("seconds");
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
    if (nudgeUnit === "frames" && !framesAvailable) setNudgeUnit("seconds");
  }, [nudgeUnit, framesAvailable]);

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
    setActiveBoundary("start");
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

  const nudgeStepValue = nudgeStepMs(nudgeUnit, nudgeValue, mediaFrameRate);

  const nudgeStart = (direction: -1 | 1) => {
    if (startMs === null) return;
    setActiveBoundary("start");
    setStart(clampBoundaryMs(startMs + direction * nudgeStepValue, mediaDurationMs));
  };

  const nudgeEnd = (direction: -1 | 1) => {
    const baseMs = endMs ?? startMs;
    if (baseMs === null) return;
    setActiveBoundary("end");
    setEnd(clampBoundaryMs(baseMs + direction * nudgeStepValue, mediaDurationMs));
  };

  const activePreview = activeBoundary === "start" ? startPreview : endPreview;

  return (
    <Stack spacing={2}>
      <Stack spacing={1} alignItems="center">
        <Tabs
          value={activeBoundary}
          onChange={(_event, value) => setActiveBoundary(value)}
          sx={{ minHeight: 36 }}
        >
          <Tab label="Start" value="start" sx={{ minHeight: 36, py: 0.5 }} />
          <Tab label="End" value="end" sx={{ minHeight: 36, py: 0.5 }} />
        </Tabs>
        <Box sx={{ width: "100%", maxWidth: 480 }}>
          <BoundaryPreviewFrame
            boundary={activeBoundary === "start" ? "Start" : "End"}
            preview={activePreview}
            crop={crop}
            exportUrl={exportFrameUrl(activePreview)}
          />
        </Box>
      </Stack>

      <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems="center" justifyContent="center" useFlexGap flexWrap="wrap">
        <Stack direction="row" spacing={0.25} alignItems="center">
          <FrameNudgeButton
            boundary="Start"
            direction="backward"
            disabled={startMs === null}
            tooltip="Nudge Start earlier by the amount set below"
            ariaLabel="Nudge start earlier"
            onClick={() => nudgeStart(-1)}
            onArrowNudge={nudgeStart}
          />
          <TimestampField
            id="clip-start-input"
            label="Start"
            value={startInput}
            error={startParse.error}
            onChange={(input) => {
              const parsed = parseTimestampMs(input);
              setStartPreview(null);
              setActiveBoundary("start");
              onStartChange(input, parsed.error ? null : parsed.value);
            }}
            onFocus={() => setActiveBoundary("start")}
            onBlur={() => {
              if (!startParse.error) commitStartPreview(startParse.value);
            }}
            startAdornment={(
              <Tooltip title="Set to current stream time">
                <span>
                  <IconButton
                    aria-label="Set Start to current stream time"
                    disabled={livePositionMs === null}
                    onClick={() => {
                      setActiveBoundary("start");
                      setStart(livePositionMs);
                    }}
                    size="small"
                    edge="start"
                    sx={{ p: 0.25 }}
                  >
                    <AddLocationAltRounded fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>
            )}
          />
          <FrameNudgeButton
            boundary="Start"
            direction="forward"
            disabled={startMs === null}
            tooltip="Nudge Start later by the amount set below"
            ariaLabel="Nudge start later"
            onClick={() => nudgeStart(1)}
            onArrowNudge={nudgeStart}
          />
        </Stack>

        <Tooltip title="Clear captured boundaries">
          <IconButton
            aria-label="Clear captured boundaries"
            onClick={() => {
              setStartPreview(null);
              setEndPreview(null);
              setStart(null);
              setEnd(null);
            }}
            sx={{ color: "text.secondary" }}
          >
            <RestartAltRounded />
          </IconButton>
        </Tooltip>

        <Stack direction="row" spacing={0.25} alignItems="center">
          <FrameNudgeButton
            boundary="End"
            direction="backward"
            disabled={endMs === null && startMs === null}
            tooltip="Nudge End earlier by the amount set below"
            ariaLabel="Nudge end earlier"
            onClick={() => nudgeEnd(-1)}
            onArrowNudge={nudgeEnd}
          />
          <TimestampField
            id="clip-end-input"
            label="End"
            value={endInput}
            error={endParse.error}
            onChange={(input) => {
              const parsed = parseTimestampMs(input);
              setEndPreview(null);
              setActiveBoundary("end");
              onEndChange(input, parsed.error ? null : parsed.value);
            }}
            onFocus={() => setActiveBoundary("end")}
            onBlur={() => {
              if (!endParse.error) commitEndPreview(endParse.value);
            }}
            endAdornment={(
              <Tooltip title="Set to current stream time">
                <span>
                  <IconButton
                    aria-label="Set End to current stream time"
                    disabled={livePositionMs === null}
                    onClick={() => {
                      setActiveBoundary("end");
                      setEnd(livePositionMs);
                    }}
                    size="small"
                    edge="end"
                    sx={{ p: 0.25 }}
                  >
                    <AddLocationAltRounded fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>
            )}
          />
          <FrameNudgeButton
            boundary="End"
            direction="forward"
            disabled={endMs === null && startMs === null}
            tooltip="Nudge End later by the amount set below"
            ariaLabel="Nudge end later"
            onClick={() => nudgeEnd(1)}
            onArrowNudge={nudgeEnd}
          />
        </Stack>
      </Stack>

      <NudgeAmountControl
        value={nudgeValue}
        unit={nudgeUnit}
        framesAvailable={framesAvailable}
        onValueChange={setNudgeValue}
        onUnitChange={setNudgeUnit}
      />

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
