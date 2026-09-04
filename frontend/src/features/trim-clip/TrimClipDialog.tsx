import AddLocationAltRounded from "@mui/icons-material/AddLocationAltRounded";
import CloseRounded from "@mui/icons-material/CloseRounded";
import PlayArrowRounded from "@mui/icons-material/PlayArrowRounded";
import RestartAltRounded from "@mui/icons-material/RestartAltRounded";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { fetchClipTrimInfo } from "../../api";
import { formatTimestampMs, parseTimestampMs } from "../../timestamps";
import type { ClipRecord } from "../../types";
import { EditTimeline } from "../editing/EditTimeline";
import {
  canShiftTimelineBoundary,
  shiftTimelineBoundary,
  type TimelineRange,
} from "../editing/timelineMath";
import { clampTrimRange, shouldStopPreview, validateTrimValue } from "./trimSelection";

interface TrimClipDialogProps {
  clip: ClipRecord;
  onClose: () => void;
}

interface FrameNudgeButtonProps {
  boundary: "Start" | "End";
  direction: "backward" | "forward";
  disabled: boolean;
  onClick: () => void;
  onArrowNudge: (direction: -1 | 1) => void;
}

function FrameNudgeButton({ boundary, direction, disabled, onClick, onArrowNudge }: FrameNudgeButtonProps) {
  const backward = direction === "backward";
  return (
    <Tooltip title={`Move ${boundary} ${direction} one nominal frame`}>
      <span>
        <Button
          aria-label={`Move ${boundary} ${direction} one frame`}
          disabled={disabled}
          variant="outlined"
          onClick={onClick}
          onKeyDown={(event) => {
            if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
            event.preventDefault();
            onArrowNudge(event.key === "ArrowLeft" ? -1 : 1);
          }}
          sx={{ minWidth: 44, width: 44, height: 56, px: 0.5, fontVariantNumeric: "tabular-nums" }}
        >
          {backward ? "−1f" : "+1f"}
        </Button>
      </span>
    </Tooltip>
  );
}

export function TrimClipDialog({ clip, onClose }: TrimClipDialogProps) {
  const theme = useTheme();
  const fullScreen = useMediaQuery(theme.breakpoints.down("sm"));
  const videoRef = useRef<HTMLVideoElement>(null);
  const initializedRef = useRef(false);
  const [durationMs, setDurationMs] = useState(Math.max(1, clip.duration_ms));
  const [startMs, setStartMs] = useState(0);
  const [endMs, setEndMs] = useState(Math.max(1, clip.duration_ms));
  const [startInput, setStartInput] = useState(formatTimestampMs(0));
  const [endInput, setEndInput] = useState(formatTimestampMs(Math.max(1, clip.duration_ms)));
  const [startError, setStartError] = useState<string | null>(null);
  const [endError, setEndError] = useState<string | null>(null);
  const [playheadMs, setPlayheadMs] = useState(0);
  const [previewing, setPreviewing] = useState(false);
  const [videoPlaying, setVideoPlaying] = useState(false);
  const [activeBoundary, setActiveBoundary] = useState<"start" | "end" | null>(null);
  const [playbackError, setPlaybackError] = useState<string | null>(null);
  const [expectedRevision, setExpectedRevision] = useState<number | null>(null);

  const info = useQuery({
    queryKey: ["clip-trim-info", clip.id, clip.revision],
    queryFn: () => fetchClipTrimInfo(clip.id),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  useEffect(() => {
    if (!info.data || initializedRef.current) return;
    initializedRef.current = true;
    const nextDuration = Math.max(1, info.data.duration_ms);
    setExpectedRevision(info.data.revision);
    setDurationMs(nextDuration);
    setStartMs(0);
    setEndMs(nextDuration);
    setStartInput(formatTimestampMs(0));
    setEndInput(formatTimestampMs(nextDuration));
  }, [info.data]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !videoPlaying) return;
    let frameCallback: number | null = null;
    const stopAtEnd = () => {
      video.pause();
      video.currentTime = endMs / 1_000;
      setPlayheadMs(endMs);
      setPreviewing(false);
      setVideoPlaying(false);
    };
    const updateFromMediaTime = (mediaTimeSeconds: number) => {
      setPlayheadMs(Math.min(durationMs, Math.round(mediaTimeSeconds * 1_000)));
      if (previewing && shouldStopPreview(mediaTimeSeconds, endMs)) stopAtEnd();
    };
    const checkCurrentTime = () => updateFromMediaTime(video.currentTime);
    const checkVideoFrame: VideoFrameRequestCallback = (_now, metadata) => {
      updateFromMediaTime(metadata.mediaTime);
      if (previewing && shouldStopPreview(metadata.mediaTime, endMs)) return;
      frameCallback = video.requestVideoFrameCallback(checkVideoFrame);
    };
    video.addEventListener("timeupdate", checkCurrentTime);
    if ("requestVideoFrameCallback" in video) {
      frameCallback = video.requestVideoFrameCallback(checkVideoFrame);
    }
    return () => {
      video.removeEventListener("timeupdate", checkCurrentTime);
      if (frameCallback !== null && "cancelVideoFrameCallback" in video) {
        video.cancelVideoFrameCallback(frameCallback);
      }
    };
  }, [durationMs, endMs, previewing, videoPlaying]);

  useEffect(() => () => videoRef.current?.pause(), []);

  const cancelPreview = () => {
    videoRef.current?.pause();
    setPreviewing(false);
    setVideoPlaying(false);
  };

  const seekPlayhead = (valueMs: number) => {
    const nextPlayhead = Math.min(durationMs, Math.max(0, Math.round(valueMs)));
    cancelPreview();
    if (videoRef.current) videoRef.current.currentTime = nextPlayhead / 1_000;
    setPlayheadMs(nextPlayhead);
  };

  const commitRange = (nextStart: number, nextEnd: number, active: "start" | "end") => {
    const next = clampTrimRange(nextStart, nextEnd, durationMs, active);
    cancelPreview();
    setStartMs(next.startMs);
    setEndMs(next.endMs);
    setStartInput(formatTimestampMs(next.startMs));
    setEndInput(formatTimestampMs(next.endMs));
    setStartError(null);
    setEndError(null);
  };

  const setStartFromInput = (input: string) => {
    setStartInput(input);
    const parsed = validateTrimValue(parseTimestampMs(input), "start", endMs, durationMs);
    setStartError(parsed.error);
    if (parsed.value !== null) commitRange(parsed.value, endMs, "start");
  };

  const setEndFromInput = (input: string) => {
    setEndInput(input);
    const parsed = validateTrimValue(parseTimestampMs(input), "end", startMs, durationMs);
    setEndError(parsed.error);
    if (parsed.value !== null) commitRange(startMs, parsed.value, "end");
  };

  const setBoundaryToPlayhead = (boundary: "start" | "end") => {
    if (boundary === "start") commitRange(playheadMs, endMs, "start");
    else commitRange(startMs, playheadMs, "end");
  };

  const previewSelection = async () => {
    const video = videoRef.current;
    if (!video) return;
    setPlaybackError(null);
    video.currentTime = startMs / 1_000;
    setPlayheadMs(startMs);
    setPreviewing(true);
    try {
      await video.play();
    } catch {
      setPreviewing(false);
      setPlaybackError("The browser could not start playback. Use the video controls and try again.");
    }
  };

  const resetRange = () => commitRange(0, durationMs, "end");
  const trimInfo = info.data;
  const frameStepMs = trimInfo?.frame_rate
    ? Math.max(1, Math.round(1_000 / trimInfo.frame_rate))
    : undefined;
  const selectionRange: TimelineRange = { startMs, endMs };
  const editableRange: TimelineRange = { startMs: 0, endMs: durationMs };

  const nudgeBoundaryOneFrame = (boundary: "start" | "end", direction: -1 | 1) => {
    if (!frameStepMs) return;
    setActiveBoundary(boundary);
    const next = shiftTimelineBoundary(selectionRange, editableRange, boundary, direction * frameStepMs);
    if (next === selectionRange) return;
    commitRange(next.startMs, next.endMs, boundary);
    seekPlayhead(boundary === "start" ? next.startMs : next.endMs);
  };

  const canNudgeBoundary = (boundary: "start" | "end", direction: -1 | 1) => Boolean(
    frameStepMs
    && canShiftTimelineBoundary(selectionRange, editableRange, boundary, direction * frameStepMs),
  );

  return (
    <Dialog open onClose={onClose} fullScreen={fullScreen} fullWidth maxWidth="lg" aria-labelledby="trim-dialog-title">
      <DialogTitle id="trim-dialog-title" sx={{ pr: 7 }}>
        Trim {trimInfo?.title ?? clip.title}
        <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1.5 }}>
          Revision {expectedRevision ?? clip.revision}
        </Typography>
        <IconButton aria-label="Close trim editor" onClick={onClose} sx={{ position: "absolute", right: 12, top: 12 }}>
          <CloseRounded />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers>
        {info.isPending ? (
          <Box sx={{ minHeight: 360, display: "grid", placeItems: "center" }}>
            <CircularProgress aria-label="Loading trim editor" />
          </Box>
        ) : info.error ? (
          <Alert severity="error">{info.error.message}</Alert>
        ) : trimInfo ? (
          <Stack spacing={2.5}>
            <Box sx={{ bgcolor: "black", borderRadius: 1, overflow: "hidden", display: "grid", placeItems: "center" }}>
              <Box
                ref={videoRef}
                component="video"
                src={`${trimInfo.play_url}?revision=${trimInfo.revision}`}
                controls
                playsInline
                onTimeUpdate={(event) => setPlayheadMs(Math.min(durationMs, Math.round(event.currentTarget.currentTime * 1_000)))}
                onSeeked={(event) => setPlayheadMs(Math.min(durationMs, Math.round(event.currentTarget.currentTime * 1_000)))}
                onPlay={() => setVideoPlaying(true)}
                onPause={() => {
                  setVideoPlaying(false);
                  setPreviewing(false);
                }}
                sx={{ display: "block", width: "100%", maxHeight: "52dvh", objectFit: "contain" }}
              />
            </Box>

            <EditTimeline
              viewportRange={editableRange}
              editableRange={editableRange}
              referenceRange={editableRange}
              selectionRange={selectionRange}
              playheadMs={playheadMs}
              stepMs={frameStepMs}
              activeBoundary={activeBoundary}
              onInteractionStart={cancelPreview}
              onActiveBoundaryChange={setActiveBoundary}
              onSelectionChange={(range, activeBoundary) => {
                commitRange(range.startMs, range.endMs, activeBoundary);
              }}
              onPlayheadChange={seekPlayhead}
            />

            <Stack direction={{ xs: "column", sm: "row" }} spacing={2} justifyContent="center">
              <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" alignItems="flex-start" justifyContent="center">
                <Tooltip title="Set Start to playhead">
                  <span>
                    <IconButton
                      aria-label="Set Start to playhead"
                      disabled={playheadMs >= endMs}
                      onClick={() => {
                        setActiveBoundary("start");
                        setBoundaryToPlayhead("start");
                      }}
                      sx={{ border: 1, borderColor: "divider", borderRadius: 1, minHeight: 56 }}
                    >
                      <AddLocationAltRounded />
                    </IconButton>
                  </span>
                </Tooltip>
                {frameStepMs && (
                  <FrameNudgeButton
                    boundary="Start"
                    direction="backward"
                    disabled={!canNudgeBoundary("start", -1)}
                    onClick={() => nudgeBoundaryOneFrame("start", -1)}
                    onArrowNudge={(direction) => nudgeBoundaryOneFrame("start", direction)}
                  />
                )}
                <TextField
                  label="Start"
                  value={startInput}
                  error={Boolean(startError)}
                  helperText={startError}
                  onChange={(event) => setStartFromInput(event.target.value)}
                  onFocus={() => setActiveBoundary("start")}
                  onBlur={() => {
                    if (startError) {
                      setStartInput(formatTimestampMs(startMs));
                      setStartError(null);
                    }
                  }}
                  sx={{ width: "17ch" }}
                />
                {frameStepMs && (
                  <FrameNudgeButton
                    boundary="Start"
                    direction="forward"
                    disabled={!canNudgeBoundary("start", 1)}
                    onClick={() => nudgeBoundaryOneFrame("start", 1)}
                    onArrowNudge={(direction) => nudgeBoundaryOneFrame("start", direction)}
                  />
                )}
              </Stack>
              <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" alignItems="flex-start" justifyContent="center">
                {frameStepMs && (
                  <FrameNudgeButton
                    boundary="End"
                    direction="backward"
                    disabled={!canNudgeBoundary("end", -1)}
                    onClick={() => nudgeBoundaryOneFrame("end", -1)}
                    onArrowNudge={(direction) => nudgeBoundaryOneFrame("end", direction)}
                  />
                )}
                <TextField
                  label="End"
                  value={endInput}
                  error={Boolean(endError)}
                  helperText={endError}
                  onChange={(event) => setEndFromInput(event.target.value)}
                  onFocus={() => setActiveBoundary("end")}
                  onBlur={() => {
                    if (endError) {
                      setEndInput(formatTimestampMs(endMs));
                      setEndError(null);
                    }
                  }}
                  sx={{ width: "17ch" }}
                />
                {frameStepMs && (
                  <FrameNudgeButton
                    boundary="End"
                    direction="forward"
                    disabled={!canNudgeBoundary("end", 1)}
                    onClick={() => nudgeBoundaryOneFrame("end", 1)}
                    onArrowNudge={(direction) => nudgeBoundaryOneFrame("end", direction)}
                  />
                )}
                <Tooltip title="Set End to playhead">
                  <span>
                    <IconButton
                      aria-label="Set End to playhead"
                      disabled={playheadMs <= startMs}
                      onClick={() => {
                        setActiveBoundary("end");
                        setBoundaryToPlayhead("end");
                      }}
                      sx={{ border: 1, borderColor: "divider", borderRadius: 1, minHeight: 56 }}
                    >
                      <AddLocationAltRounded />
                    </IconButton>
                  </span>
                </Tooltip>
              </Stack>
            </Stack>

            {!trimInfo.frame_rate && (
              <Alert severity="warning">
                The clip's frame rate is unavailable, so nominal one-frame nudging is disabled.
              </Alert>
            )}
            <Box sx={{ textAlign: "center" }}>
              <Button startIcon={<RestartAltRounded />} variant="outlined" onClick={resetRange}>
                Reset
              </Button>
            </Box>
            {trimInfo.frame_rate && (
              <Typography variant="caption" color="text.secondary" sx={{ textAlign: "center" }}>
                Frame nudges use a nominal {trimInfo.frame_rate.toFixed(3)} fps duration; decoded frame boundaries may vary for VFR media.
              </Typography>
            )}

            <Typography color="text.secondary" sx={{ textAlign: "center", fontVariantNumeric: "tabular-nums" }}>
              Selected duration {formatTimestampMs(endMs - startMs)} · Playhead {formatTimestampMs(playheadMs)}
            </Typography>
            {playbackError && <Alert severity="error">{playbackError}</Alert>}
          </Stack>
        ) : null}
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose}>Close</Button>
        <Button
          variant="contained"
          startIcon={<PlayArrowRounded />}
          disabled={!trimInfo || Boolean(startError || endError)}
          onClick={() => void previewSelection()}
        >
          {previewing ? "Restart Preview" : "Preview Selection"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
