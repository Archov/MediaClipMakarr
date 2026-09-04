import AddLocationAltRounded from "@mui/icons-material/AddLocationAltRounded";
import RestartAltRounded from "@mui/icons-material/RestartAltRounded";
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
import { type ReactNode, useEffect, useState } from "react";

import { sessionFrameUrl } from "../../api";
import { formatTimestampMs, parseTimestampMs } from "../../timestamps";
import { BoundaryNudgeControls } from "./BoundaryNudgeControls";
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
  const [startPreview, setStartPreview] = useState<BoundaryPreview | null>(null);
  const [endPreview, setEndPreview] = useState<BoundaryPreview | null>(null);
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

      <BoundaryNudgeControls
        startMs={startMs}
        endMs={endMs}
        maximumMs={mediaDurationMs}
        frameRate={mediaFrameRate}
        onStartChange={setStart}
        onEndChange={setEnd}
        extraAction={(
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
        )}
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
