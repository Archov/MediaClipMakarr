import AddLocationAltRounded from "@mui/icons-material/AddLocationAltRounded";
import ArrowBackIosRounded from "@mui/icons-material/ArrowBackIosRounded";
import ArrowForwardIosRounded from "@mui/icons-material/ArrowForwardIosRounded";
import CloseRounded from "@mui/icons-material/CloseRounded";
import ContentCopyRounded from "@mui/icons-material/ContentCopyRounded";
import GifRounded from "@mui/icons-material/GifRounded";
import PlayArrowRounded from "@mui/icons-material/PlayArrowRounded";
import RestartAltRounded from "@mui/icons-material/RestartAltRounded";
import SaveRounded from "@mui/icons-material/SaveRounded";
import VolumeOffRounded from "@mui/icons-material/VolumeOffRounded";
import VolumeUpRounded from "@mui/icons-material/VolumeUpRounded";
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
  LinearProgress,
  Stack,
  TextField,
  Tooltip,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { fetchClipTrimInfo, saveClipTrim } from "../../api";
import { formatTimestampMs, parseTimestampMs } from "../../timestamps";
import type { ClipRecord } from "../../types";
import { EditTimeline } from "../editing/EditTimeline";
import { useGifExport } from "../gif-export/useGifExport";
import { useJobSnapshot } from "../make-clip/hooks";
import {
  canShiftTimelineBoundary,
  shiftTimelineBoundary,
  type TimelineRange,
} from "../editing/timelineMath";
import { clampTrimRange, shouldStopPreview, validateTrimValue } from "./trimSelection";

// Matches the theme-matched accent blue used elsewhere in the app (see
// IMMICH_ICON_BLUE in LibraryScreen.tsx).
const ACCENT_BLUE = "#61a6fa";

interface TrimClipDialogProps {
  clip: ClipRecord;
  onClose: () => void;
}

interface TimestampFieldProps {
  id: string;
  label: string;
  value: string;
  error: string | null;
  onChange: (value: string) => void;
  onFocus: () => void;
  onBlur: () => void;
  startAdornment?: ReactNode;
  endAdornment?: ReactNode;
}

function TimestampField({
  id,
  label,
  value,
  error,
  onChange,
  onFocus,
  onBlur,
  startAdornment,
  endAdornment,
}: TimestampFieldProps) {
  return (
    <TextField
      id={id}
      label={label}
      value={value}
      error={Boolean(error)}
      helperText={error}
      onChange={(event) => onChange(event.target.value)}
      onFocus={onFocus}
      onBlur={onBlur}
      size="small"
      slotProps={{
        input: { startAdornment, endAdornment },
        htmlInput: {
          style: { width: "12ch", textAlign: "center", fontVariantNumeric: "tabular-nums" },
        },
      }}
      sx={{
        width: "fit-content",
        // Centers the label over the input instead of MUI's default left-aligned
        // notch, by centering the real notch (the fieldset's `legend`, which
        // natively cuts the border gap) rather than hiding it behind a faked
        // background patch — the latter can't match this theme's dark-mode Paper
        // elevation overlay, which layers a translucent gradient over the base
        // background color rather than being one flat color.
        "& .MuiInputLabel-root": {
          right: 0,
          textAlign: "center",
        },
        "& .MuiInputLabel-shrink": {
          left: 0,
          right: 0,
          width: "fit-content",
          margin: "0 auto",
          whiteSpace: "nowrap",
          textAlign: "center",
          // MUI's default shrink transform is translate(14px, -9px) scale(0.75).
          // The -9px vertical offset is what correctly bisects the border line, so
          // it's kept as-is; the 14px horizontal offset is dropped since it fights
          // the left/right/margin centering above. transformOrigin must also move
          // to the element's center — MUI's default is the top-left corner, which
          // scales the box toward that corner instead of shrinking it in place,
          // silently un-centering it.
          transform: "translate(0, -9px) scale(0.75)",
          transformOrigin: "center",
        },
        "& .MuiOutlinedInput-root legend": {
          float: "none",
          margin: "0 auto",
          // MUI sets the notch width via an inline style, which needs !important
          // to override.
          width: "fit-content !important",
        },
      }}
    />
  );
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
        <IconButton
          aria-label={`Move ${boundary} ${direction} one frame`}
          disabled={disabled}
          onClick={onClick}
          onKeyDown={(event) => {
            if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
            event.preventDefault();
            onArrowNudge(event.key === "ArrowLeft" ? -1 : 1);
          }}
          size="small"
          sx={{
            p: 0,
            color: disabled ? "text.disabled" : ACCENT_BLUE,
          }}
        >
          {backward ? (
            <ArrowBackIosRounded style={{ fontSize: 32 }} />
          ) : (
            <ArrowForwardIosRounded style={{ fontSize: 32 }} />
          )}
        </IconButton>
      </span>
    </Tooltip>
  );
}

export function TrimClipDialog({ clip, onClose }: TrimClipDialogProps) {
  const theme = useTheme();
  const fullScreen = useMediaQuery(theme.breakpoints.down("sm"));
  const videoRef = useRef<HTMLVideoElement>(null);
  const queryClient = useQueryClient();
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
  const [muted, setMuted] = useState(false);
  const [activeBoundary, setActiveBoundary] = useState<"start" | "end" | null>(null);
  const [playbackError, setPlaybackError] = useState<string | null>(null);
  const [expectedRevision, setExpectedRevision] = useState<number | null>(null);
  const [submittedJob, setSubmittedJob] = useState<import("../../types").JobSnapshot | null>(null);
  const [confirmingReplace, setConfirmingReplace] = useState(false);

  const saveMutation = useMutation({
    mutationFn: (mode: "new" | "replace") => {
      if (expectedRevision === null) throw new Error("The opening clip revision is unavailable.");
      return saveClipTrim(clip.id, {
        start_ms: startMs,
        end_ms: endMs,
        expected_revision: expectedRevision,
        mode,
      });
    },
    onMutate: () => {
      cancelPreview();
      setSubmittedJob(null);
    },
    onSuccess: (job) => {
      setConfirmingReplace(false);
      setSubmittedJob(job);
    },
  });
  const activeJob = useJobSnapshot(submittedJob);
  const jobBusy = Boolean(
    activeJob && ["QUEUED", "RUNNING", "FINALIZING"].includes(activeJob.state),
  );
  const saving = saveMutation.isPending || jobBusy;
  const gifExport = useGifExport(clip.id);

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

  useEffect(() => {
    if (activeJob?.state !== "SUCCEEDED") return;
    void queryClient.invalidateQueries({ queryKey: ["clips"] });
    void queryClient.invalidateQueries({ queryKey: ["clip", clip.id] });
    void queryClient.invalidateQueries({ queryKey: ["clip-libraries"] });
    onClose();
  }, [activeJob?.state, clip.id, queryClient, onClose]);

  const cancelPreview = () => {
    videoRef.current?.pause();
    setPreviewing(false);
    setVideoPlaying(false);
  };

  const togglePlayback = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) void video.play();
    else video.pause();
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
  const selectionChanged = startMs > 0 || endMs < durationMs;
  const saveDisabled = !trimInfo || expectedRevision === null
    || Boolean(startError || endError) || !selectionChanged || saving
    || activeJob?.state === "SUCCEEDED";

  return (
    <>
    <Dialog open onClose={onClose} fullScreen={fullScreen} fullWidth maxWidth="lg" aria-labelledby="trim-dialog-title">
      <DialogTitle id="trim-dialog-title" sx={{ pr: 7, py: 1, fontSize: "1rem", fontWeight: 600, lineHeight: 1.3 }}>
        <Tooltip title={`Trim ${trimInfo?.title ?? clip.title}`} enterTouchDelay={0}>
          <Box
            component="span"
            sx={{
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            Trim {trimInfo?.title ?? clip.title}
          </Box>
        </Tooltip>
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
            <Box sx={{ position: "relative", bgcolor: "black", borderRadius: 1, overflow: "hidden", display: "grid", placeItems: "center" }}>
              <Box
                ref={videoRef}
                component="video"
                src={`${trimInfo.play_url}?revision=${trimInfo.revision}`}
                playsInline
                muted={muted}
                onClick={togglePlayback}
                onTimeUpdate={(event) => setPlayheadMs(Math.min(durationMs, Math.round(event.currentTarget.currentTime * 1_000)))}
                onSeeked={(event) => setPlayheadMs(Math.min(durationMs, Math.round(event.currentTarget.currentTime * 1_000)))}
                onPlay={() => setVideoPlaying(true)}
                onPause={() => {
                  setVideoPlaying(false);
                  setPreviewing(false);
                }}
                sx={{ display: "block", width: "100%", maxHeight: "52dvh", objectFit: "contain", cursor: "pointer" }}
              />
              <Tooltip title={muted ? "Unmute" : "Mute"}>
                <IconButton
                  aria-label={muted ? "Unmute" : "Mute"}
                  onClick={() => setMuted((value) => !value)}
                  sx={{
                    position: "absolute",
                    right: 8,
                    bottom: 8,
                    color: "common.white",
                    bgcolor: "rgba(0, 0, 0, 0.5)",
                    "&:hover": { bgcolor: "rgba(0, 0, 0, 0.7)" },
                  }}
                >
                  {muted ? <VolumeOffRounded /> : <VolumeUpRounded />}
                </IconButton>
              </Tooltip>
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

            <Typography
              color="text.secondary"
              sx={{ textAlign: "center", fontVariantNumeric: "tabular-nums", fontSize: "0.85rem" }}
            >
              Length {formatTimestampMs(endMs - startMs)} · At {formatTimestampMs(playheadMs)}
            </Typography>

            <Stack direction={{ xs: "column", sm: "row" }} spacing={0.25} alignItems="center" justifyContent="center">
              <Stack direction="row" spacing={0.25} alignItems="center" justifyContent="center">
                {frameStepMs && (
                  <FrameNudgeButton
                    boundary="Start"
                    direction="backward"
                    disabled={!canNudgeBoundary("start", -1)}
                    onClick={() => nudgeBoundaryOneFrame("start", -1)}
                    onArrowNudge={(direction) => nudgeBoundaryOneFrame("start", direction)}
                  />
                )}
                <TimestampField
                  id="trim-start-input"
                  label="Start"
                  value={startInput}
                  error={startError}
                  onChange={setStartFromInput}
                  onFocus={() => setActiveBoundary("start")}
                  onBlur={() => {
                    if (startError) {
                      setStartInput(formatTimestampMs(startMs));
                      setStartError(null);
                    }
                  }}
                  startAdornment={(
                    <Tooltip title="Set Start to playhead">
                      <span>
                        <IconButton
                          aria-label="Set Start to playhead"
                          disabled={playheadMs >= endMs}
                          onClick={() => {
                            setActiveBoundary("start");
                            setBoundaryToPlayhead("start");
                          }}
                          size="small"
                          edge="start"
                          sx={{
                            p: 0.25,
                            color: playheadMs >= endMs ? "text.disabled" : ACCENT_BLUE,
                          }}
                        >
                          <AddLocationAltRounded fontSize="small" />
                        </IconButton>
                      </span>
                    </Tooltip>
                  )}
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
              <Tooltip title="Reset to full clip">
                <span>
                  <IconButton
                    aria-label="Reset to full clip"
                    onClick={resetRange}
                    size="small"
                    sx={{ p: 0.25, color: ACCENT_BLUE }}
                  >
                    <RestartAltRounded />
                  </IconButton>
                </span>
              </Tooltip>
              <Stack direction="row" spacing={0.25} alignItems="center" justifyContent="center">
                {frameStepMs && (
                  <FrameNudgeButton
                    boundary="End"
                    direction="backward"
                    disabled={!canNudgeBoundary("end", -1)}
                    onClick={() => nudgeBoundaryOneFrame("end", -1)}
                    onArrowNudge={(direction) => nudgeBoundaryOneFrame("end", direction)}
                  />
                )}
                <TimestampField
                  id="trim-end-input"
                  label="End"
                  value={endInput}
                  error={endError}
                  onChange={setEndFromInput}
                  onFocus={() => setActiveBoundary("end")}
                  onBlur={() => {
                    if (endError) {
                      setEndInput(formatTimestampMs(endMs));
                      setEndError(null);
                    }
                  }}
                  endAdornment={(
                    <Tooltip title="Set End to playhead">
                      <span>
                        <IconButton
                          aria-label="Set End to playhead"
                          disabled={playheadMs <= startMs}
                          onClick={() => {
                            setActiveBoundary("end");
                            setBoundaryToPlayhead("end");
                          }}
                          size="small"
                          edge="end"
                          sx={{
                            p: 0.25,
                            color: playheadMs <= startMs ? "text.disabled" : ACCENT_BLUE,
                          }}
                        >
                          <AddLocationAltRounded fontSize="small" />
                        </IconButton>
                      </span>
                    </Tooltip>
                  )}
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
              </Stack>
            </Stack>

            {!trimInfo.frame_rate && (
              <Alert severity="warning">
                The clip's frame rate is unavailable, so nominal one-frame nudging is disabled.
              </Alert>
            )}

            <Box sx={{ textAlign: "center" }}>
              <Button
                variant="contained"
                startIcon={<PlayArrowRounded />}
                disabled={!trimInfo || Boolean(startError || endError) || saving}
                onClick={() => void previewSelection()}
                sx={{ minWidth: 140 }}
              >
                {previewing ? "Restart" : "Preview"}
              </Button>
            </Box>
            {playbackError && <Alert severity="error">{playbackError}</Alert>}
            {activeJob && (
              <Alert severity={activeJob.state === "FAILED" ? "error" : activeJob.state === "SUCCEEDED" ? "success" : "info"}>
                {activeJob.error?.message ?? activeJob.message}
              </Alert>
            )}
            {saveMutation.error && <Alert severity="error">{saveMutation.error.message}</Alert>}
            {gifExport.error && <Alert severity="error">{gifExport.error}</Alert>}
            {jobBusy && (
              <LinearProgress
                variant="determinate"
                value={Math.round((activeJob?.progress ?? 0) * 100)}
                aria-label="Trim save progress"
              />
            )}
          </Stack>
        ) : null}
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2, flexWrap: "wrap", gap: 1 }}>
        <Button
          variant="outlined"
          startIcon={<GifRounded />}
          disabled={gifExport.busy || endMs <= startMs}
          onClick={() => gifExport.exportGif({ startMs, endMs })}
        >
          {gifExport.busy ? "Exporting GIF…" : "Export GIF"}
        </Button>
        <Button
          variant="contained"
          startIcon={<ContentCopyRounded />}
          disabled={saveDisabled}
          onClick={() => saveMutation.mutate("new")}
        >
          Save as New
        </Button>
        <Button
          color="error"
          variant="outlined"
          startIcon={<SaveRounded />}
          disabled={saveDisabled}
          onClick={() => setConfirmingReplace(true)}
        >
          Replace
        </Button>
      </DialogActions>
    </Dialog>
    <Dialog
      open={confirmingReplace}
      onClose={() => setConfirmingReplace(false)}
      maxWidth="sm"
      fullWidth
      aria-labelledby="confirm-replace-title"
    >
      <DialogTitle id="confirm-replace-title">Replace existing clip?</DialogTitle>
      <DialogContent>
        <Alert severity="warning">
          Replace the existing clip only after the new render passes validation. The clip keeps
          its identity and advances to the next revision.
        </Alert>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => setConfirmingReplace(false)}>Cancel</Button>
        <Button
          color="error"
          variant="contained"
          onClick={() => {
            setConfirmingReplace(false);
            saveMutation.mutate("replace");
          }}
        >
          Confirm Replace
        </Button>
      </DialogActions>
    </Dialog>
    </>
  );
}
