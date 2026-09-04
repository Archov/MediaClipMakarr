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
  Slider,
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
import { BoundaryNudgeControls } from "../make-clip/BoundaryNudgeControls";
import { clampTrimRange, shouldStopPreview, validateTrimValue } from "./trimSelection";

interface TrimClipDialogProps {
  clip: ClipRecord;
  onClose: () => void;
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
    if (!video || !previewing) return;
    let frameCallback: number | null = null;
    const stopAtEnd = () => {
      video.pause();
      video.currentTime = endMs / 1_000;
      setPlayheadMs(endMs);
      setPreviewing(false);
    };
    const checkCurrentTime = () => {
      if (shouldStopPreview(video.currentTime, endMs)) stopAtEnd();
    };
    const checkVideoFrame: VideoFrameRequestCallback = (_now, metadata) => {
      if (shouldStopPreview(metadata.mediaTime, endMs)) {
        stopAtEnd();
        return;
      }
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
  }, [endMs, previewing]);

  useEffect(() => () => videoRef.current?.pause(), []);

  const cancelPreview = () => {
    videoRef.current?.pause();
    setPreviewing(false);
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
                onPause={() => setPreviewing(false)}
                sx={{ display: "block", width: "100%", maxHeight: "52dvh", objectFit: "contain" }}
              />
            </Box>

            <Stack spacing={0.5}>
              <Slider
                value={[startMs, endMs]}
                min={0}
                max={durationMs}
                step={1}
                disableSwap
                valueLabelDisplay="auto"
                valueLabelFormat={(value) => formatTimestampMs(value)}
                getAriaLabel={(index) => index === 0 ? "Trim start" : "Trim end"}
                onChange={(_event, value, activeThumb) => {
                  const [nextStart, nextEnd] = value as number[];
                  commitRange(nextStart, nextEnd, activeThumb === 0 ? "start" : "end");
                }}
              />
              <Stack direction="row" justifyContent="space-between">
                <Typography variant="caption" color="text.secondary">00:00:00.000</Typography>
                <Typography variant="caption" color="text.secondary">{formatTimestampMs(durationMs)}</Typography>
              </Stack>
            </Stack>

            <Stack direction={{ xs: "column", sm: "row" }} spacing={2} justifyContent="center">
              <Stack direction="row" spacing={1} alignItems="flex-start" justifyContent="center">
                <TextField
                  label="Start"
                  value={startInput}
                  error={Boolean(startError)}
                  helperText={startError}
                  onChange={(event) => setStartFromInput(event.target.value)}
                  onBlur={() => {
                    if (startError) {
                      setStartInput(formatTimestampMs(startMs));
                      setStartError(null);
                    }
                  }}
                  sx={{ width: "17ch" }}
                />
                <Tooltip title="Set Start to playhead">
                  <span>
                    <IconButton
                      aria-label="Set Start to playhead"
                      disabled={playheadMs >= endMs}
                      onClick={() => setBoundaryToPlayhead("start")}
                      sx={{ border: 1, borderColor: "divider", borderRadius: 1, minHeight: 56 }}
                    >
                      <AddLocationAltRounded />
                    </IconButton>
                  </span>
                </Tooltip>
              </Stack>
              <Stack direction="row" spacing={1} alignItems="flex-start" justifyContent="center">
                <TextField
                  label="End"
                  value={endInput}
                  error={Boolean(endError)}
                  helperText={endError}
                  onChange={(event) => setEndFromInput(event.target.value)}
                  onBlur={() => {
                    if (endError) {
                      setEndInput(formatTimestampMs(endMs));
                      setEndError(null);
                    }
                  }}
                  sx={{ width: "17ch" }}
                />
                <Tooltip title="Set End to playhead">
                  <span>
                    <IconButton
                      aria-label="Set End to playhead"
                      disabled={playheadMs <= startMs}
                      onClick={() => setBoundaryToPlayhead("end")}
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
                The clip's frame rate is unavailable. Nudge controls default to seconds instead of nominal frame durations.
              </Alert>
            )}
            <BoundaryNudgeControls
              startMs={startMs}
              endMs={endMs}
              maximumMs={durationMs}
              frameRate={trimInfo.frame_rate}
              defaultUnit="frames"
              defaultValue={5}
              onStartChange={(value) => commitRange(value, endMs, "start")}
              onEndChange={(value) => commitRange(startMs, value, "end")}
              extraAction={(
                <Button startIcon={<RestartAltRounded />} variant="outlined" onClick={resetRange}>
                  Reset
                </Button>
              )}
            />
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
