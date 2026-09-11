import AddLocationAltRounded from "@mui/icons-material/AddLocationAltRounded";
import CloseRounded from "@mui/icons-material/CloseRounded";
import ContentCopyRounded from "@mui/icons-material/ContentCopyRounded";
import DoubleArrowRounded from "@mui/icons-material/DoubleArrowRounded";
import GifRounded from "@mui/icons-material/GifRounded";
import LibraryMusicRounded from "@mui/icons-material/LibraryMusicRounded";
import PlayArrowRounded from "@mui/icons-material/PlayArrowRounded";
import RestartAltRounded from "@mui/icons-material/RestartAltRounded";
import SaveRounded from "@mui/icons-material/SaveRounded";
import SubtitlesRounded from "@mui/icons-material/SubtitlesRounded";
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
  Menu,
  MenuItem,
  Stack,
  Tooltip,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { CountdownCircleTimer } from "react-countdown-circle-timer";

import {
  deleteClipTrimPreview,
  fetchClipSourceTracks,
  fetchClipTrimInfo,
  requestClipTrimPreview,
  saveClipTrim,
} from "../../api";
import { formatTimestampMs, parseTimestampMs } from "../../timestamps";
import type { ClipRecord, JobSnapshot } from "../../types";
import { ACCENT_BLUE, FrameNudgeButton, TimestampField } from "../editing/BoundaryFieldControls";
import { EditTimeline } from "../editing/EditTimeline";
import { useGifExport } from "../gif-export/useGifExport";
import { trackLabel } from "../make-clip/MediaTrackSelectors";
import { useJobSnapshot, useRenderDuration } from "../make-clip/hooks";
import { formatElapsedSeconds } from "../make-clip/renderDuration";
import {
  canShiftTimelineBoundary,
  formatTimelineRulerTime,
  shiftTimelineBoundary,
  type TimelineRange,
} from "../editing/timelineMath";
import { clampTrimRange, shouldStopPreview, validateTrimValue } from "./trimSelection";

const RENDER_DELAY_MS = 5_000;
const EXTEND_STEP_MS = 5_000;

interface TrimClipDialogProps {
  clip: ClipRecord;
  onClose: () => void;
}

// A track override of `"off"` means explicitly disabled/off; `undefined`
// means "leave whatever the clip already has" (the common case — most
// trims never touch audio/subtitles at all).
type TrackOverride = number | "off" | undefined;

export function TrimClipDialog({ clip, onClose }: TrimClipDialogProps) {
  const theme = useTheme();
  const fullScreen = useMediaQuery(theme.breakpoints.down("sm"));
  const videoRef = useRef<HTMLVideoElement>(null);
  const queryClient = useQueryClient();
  const initializedRef = useRef(false);
  const renderTimerRef = useRef<number | null>(null);
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
  const [submittedJob, setSubmittedJob] = useState<JobSnapshot | null>(null);
  const [confirmingReplace, setConfirmingReplace] = useState(false);

  // Extend / track-change / fast-preview state.
  const [previewToken] = useState(() => crypto.randomUUID());
  const [extendBeforeMs, setExtendBeforeMs] = useState(0);
  const [extendAfterMs, setExtendAfterMs] = useState(0);
  const [audioOverride, setAudioOverride] = useState<TrackOverride>(undefined);
  const [subtitleOverride, setSubtitleOverride] = useState<TrackOverride>(undefined);
  const [pendingRender, setPendingRender] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [renderKey, setRenderKey] = useState(0);
  const [previewOriginMs, setPreviewOriginMs] = useState(0);
  const [previewVersion, setPreviewVersion] = useState(0);
  const [previewPlayUrl, setPreviewPlayUrl] = useState<string | null>(null);
  const [previewRenderError, setPreviewRenderError] = useState<string | null>(null);
  const [confirmingReset, setConfirmingReset] = useState(false);
  const [audioMenuAnchor, setAudioMenuAnchor] = useState<HTMLElement | null>(null);
  const [subtitleMenuAnchor, setSubtitleMenuAnchor] = useState<HTMLElement | null>(null);

  const buildOverridePayload = () => ({
    ...(audioOverride !== undefined
      ? audioOverride === "off"
        ? { audio_disabled: true }
        : { audio_stream_index: audioOverride }
      : {}),
    ...(subtitleOverride !== undefined
      ? subtitleOverride === "off"
        ? { subtitles_enabled: false }
        : { subtitle_stream_index: subtitleOverride, subtitles_enabled: true }
      : {}),
  });

  const saveMutation = useMutation({
    mutationFn: (mode: "new" | "replace") => {
      if (expectedRevision === null) throw new Error("The opening clip revision is unavailable.");
      return saveClipTrim(clip.id, {
        start_ms: startMs,
        end_ms: endMs,
        expected_revision: expectedRevision,
        mode,
        ...buildOverridePayload(),
      });
    },
    onMutate: () => {
      cancelPreview();
      cancelScheduledRender();
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
  const renderDurationMs = useRenderDuration(activeJob);

  const info = useQuery({
    queryKey: ["clip-trim-info", clip.id, clip.revision],
    queryFn: () => fetchClipTrimInfo(clip.id),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  const sourceTracks = useQuery({
    queryKey: ["clip-source-tracks", clip.id, clip.revision],
    queryFn: () => fetchClipSourceTracks(clip.id),
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

  const editableRange: TimelineRange = { startMs: -extendBeforeMs, endMs: durationMs + extendAfterMs };
  const referenceRange: TimelineRange = { startMs: 0, endMs: durationMs };
  const selectionRange: TimelineRange = { startMs, endMs };
  const clampToEditable = (valueMs: number) =>
    Math.min(editableRange.endMs, Math.max(editableRange.startMs, Math.round(valueMs)));
  // The currently loaded <video> may be the original managed clip (origin 0)
  // or a rendered preview covering a wider range starting at whatever
  // editableRange.startMs was when that preview was generated — frozen with
  // it, not recomputed live, since extend amounts may keep changing before
  // the next preview replaces it. See "Preview video has its own coordinate
  // origin" in the design notes.
  const toVideoMs = (timelineMs: number) => timelineMs - previewOriginMs;
  const toTimelineMs = (videoMs: number) => videoMs + previewOriginMs;
  // Everything shown to the user (Start/End fields, the playhead readout,
  // ruler ticks) is relative to the current editable range's own start
  // rather than the original clip's start — once extended before, "0" is
  // wherever the granted room now begins, not the original clip boundary,
  // so the displayed numbers never go negative and always describe the
  // currently previewable footage.
  const toDisplayMs = (timelineMs: number) => timelineMs - editableRange.startMs;
  const toTimelineFromDisplayMs = (displayMs: number) => displayMs + editableRange.startMs;

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !videoPlaying) return;
    let frameCallback: number | null = null;
    const stopAtEnd = () => {
      video.pause();
      video.currentTime = toVideoMs(endMs) / 1_000;
      setPlayheadMs(endMs);
      setPreviewing(false);
      setVideoPlaying(false);
    };
    const updateFromMediaTime = (mediaTimeSeconds: number) => {
      setPlayheadMs(clampToEditable(toTimelineMs(Math.round(mediaTimeSeconds * 1_000))));
      if (previewing && shouldStopPreview(mediaTimeSeconds, toVideoMs(endMs))) stopAtEnd();
    };
    const checkCurrentTime = () => updateFromMediaTime(video.currentTime);
    const checkVideoFrame: VideoFrameRequestCallback = (_now, metadata) => {
      updateFromMediaTime(metadata.mediaTime);
      if (previewing && shouldStopPreview(metadata.mediaTime, toVideoMs(endMs))) return;
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [durationMs, endMs, previewing, videoPlaying, previewOriginMs, extendBeforeMs, extendAfterMs]);

  useEffect(() => () => videoRef.current?.pause(), []);

  // Best-effort cleanup of the preview scratch file — the server's own
  // stale-job-workdir reaper is the safety net if this never fires (e.g. the
  // tab was closed rather than the dialog).
  useEffect(() => {
    return () => {
      if (renderTimerRef.current !== null) window.clearTimeout(renderTimerRef.current);
      void deleteClipTrimPreview(clip.id, previewToken).catch(() => undefined);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  const cancelScheduledRender = () => {
    if (renderTimerRef.current !== null) {
      window.clearTimeout(renderTimerRef.current);
      renderTimerRef.current = null;
    }
    setPendingRender(false);
  };

  const fireRender = async () => {
    cancelScheduledRender();
    setRendering(true);
    setPreviewRenderError(null);
    const requestStartMs = -extendBeforeMs;
    const requestEndMs = durationMs + extendAfterMs;
    try {
      const response = await requestClipTrimPreview(clip.id, {
        start_ms: requestStartMs,
        end_ms: requestEndMs,
        preview_token: previewToken,
        ...buildOverridePayload(),
      });
      setPreviewOriginMs(requestStartMs);
      setPreviewVersion((value) => value + 1);
      setPreviewPlayUrl(response.play_url);
    } catch (error) {
      setPreviewRenderError(
        error instanceof Error ? error.message : "The preview could not be rendered.",
      );
    } finally {
      setRendering(false);
    }
  };

  const scheduleRender = () => {
    if (renderTimerRef.current !== null) window.clearTimeout(renderTimerRef.current);
    setPreviewRenderError(null);
    setPendingRender(true);
    setRenderKey((value) => value + 1);
    renderTimerRef.current = window.setTimeout(() => {
      renderTimerRef.current = null;
      void fireRender();
    }, RENDER_DELAY_MS);
  };

  const togglePlayback = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) void video.play();
    else video.pause();
  };

  const seekPlayhead = (valueMs: number) => {
    const nextPlayhead = clampToEditable(valueMs);
    cancelPreview();
    if (videoRef.current) videoRef.current.currentTime = toVideoMs(nextPlayhead) / 1_000;
    setPlayheadMs(nextPlayhead);
  };

  // `displayOriginMsOverride` is only needed when a caller is changing
  // `extendBeforeMs` in this same tick (bumpExtend's "before" branch): the
  // live `editableRange.startMs` computed earlier in this render still
  // reflects the *previous* extend amount, so the caller passes the
  // about-to-be-committed origin explicitly instead.
  const applyRange = (next: TimelineRange, displayOriginMsOverride?: number) => {
    const displayOrigin = displayOriginMsOverride ?? editableRange.startMs;
    cancelPreview();
    setStartMs(next.startMs);
    setEndMs(next.endMs);
    setStartInput(formatTimestampMs(next.startMs - displayOrigin));
    setEndInput(formatTimestampMs(next.endMs - displayOrigin));
    setStartError(null);
    setEndError(null);
  };

  const commitRange = (nextStart: number, nextEnd: number, active: "start" | "end") => {
    applyRange(
      clampTrimRange(nextStart, nextEnd, durationMs + extendAfterMs, active, -extendBeforeMs),
    );
  };

  const setStartFromInput = (input: string) => {
    setStartInput(input);
    const parsed = validateTrimValue(
      parseTimestampMs(input),
      "start",
      toDisplayMs(endMs),
      editableRange.endMs - editableRange.startMs,
    );
    setStartError(parsed.error);
    if (parsed.value !== null) commitRange(toTimelineFromDisplayMs(parsed.value), endMs, "start");
  };

  const setEndFromInput = (input: string) => {
    setEndInput(input);
    const parsed = validateTrimValue(
      parseTimestampMs(input),
      "end",
      toDisplayMs(startMs),
      editableRange.endMs - editableRange.startMs,
    );
    setEndError(parsed.error);
    if (parsed.value !== null) commitRange(startMs, toTimelineFromDisplayMs(parsed.value), "end");
  };

  const setBoundaryToPlayhead = (boundary: "start" | "end") => {
    if (boundary === "start") commitRange(playheadMs, endMs, "start");
    else commitRange(startMs, playheadMs, "end");
  };

  const previewSelection = async () => {
    const video = videoRef.current;
    if (!video) return;
    setPlaybackError(null);
    video.currentTime = toVideoMs(startMs) / 1_000;
    setPlayheadMs(startMs);
    setPreviewing(true);
    try {
      await video.play();
    } catch {
      setPreviewing(false);
      setPlaybackError("The browser could not start playback. Use the video controls and try again.");
    }
  };

  const trimInfo = info.data;
  const extendInfo = sourceTracks.data;
  const extendAvailable = extendInfo?.available ?? false;
  const maxExtendBeforeMs = extendInfo?.max_extend_before_ms ?? 0;
  const maxExtendAfterMs = extendInfo?.max_extend_after_ms ?? 0;
  const pendingChanges = extendBeforeMs > 0 || extendAfterMs > 0 || audioOverride !== undefined || subtitleOverride !== undefined;
  const extendControlsDisabled = !extendAvailable || rendering;

  const bumpExtend = (side: "before" | "after") => {
    if (side === "before") {
      const next = Math.min(maxExtendBeforeMs, extendBeforeMs + EXTEND_STEP_MS);
      if (next === extendBeforeMs) return;
      setExtendBeforeMs(next);
      // extendBeforeMs (and so the display origin, -extendBeforeMs) is
      // changing in this same tick — the live `editableRange.startMs` above
      // still reflects the old amount, so pass the new one explicitly.
      applyRange(clampTrimRange(-next, endMs, durationMs + extendAfterMs, "start", -next), -next);
    } else {
      const next = Math.min(maxExtendAfterMs, extendAfterMs + EXTEND_STEP_MS);
      if (next === extendAfterMs) return;
      setExtendAfterMs(next);
      applyRange(
        clampTrimRange(startMs, durationMs + next, durationMs + next, "end", -extendBeforeMs),
      );
    }
    scheduleRender();
  };

  const applyAudioOverride = (value: TrackOverride) => {
    setAudioOverride(value);
    setAudioMenuAnchor(null);
    scheduleRender();
  };

  const applySubtitleOverride = (value: TrackOverride) => {
    setSubtitleOverride(value);
    setSubtitleMenuAnchor(null);
    scheduleRender();
  };

  const performReset = () => {
    cancelScheduledRender();
    setExtendBeforeMs(0);
    setExtendAfterMs(0);
    setAudioOverride(undefined);
    setSubtitleOverride(undefined);
    setPreviewOriginMs(0);
    setPreviewVersion(0);
    setPreviewPlayUrl(null);
    setPreviewRenderError(null);
    applyRange(clampTrimRange(0, durationMs, durationMs, "end"));
  };

  const resetRange = () => {
    if (pendingChanges) {
      setConfirmingReset(true);
      return;
    }
    performReset();
  };

  const frameStepMs = trimInfo?.frame_rate
    ? Math.max(1, Math.round(1_000 / trimInfo.frame_rate))
    : undefined;

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
  const selectionChanged = startMs !== 0 || endMs !== durationMs || pendingChanges;
  const saveDisabled = !trimInfo || expectedRevision === null
    || Boolean(startError || endError) || !selectionChanged || saving
    || activeJob?.state === "SUCCEEDED";
  const videoSrc = previewPlayUrl
    ? `${previewPlayUrl}?v=${previewVersion}`
    : trimInfo
      ? `${trimInfo.play_url}?revision=${trimInfo.revision}`
      : undefined;

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
                src={videoSrc}
                playsInline
                muted={muted}
                onClick={togglePlayback}
                onTimeUpdate={(event) => setPlayheadMs(clampToEditable(toTimelineMs(Math.round(event.currentTarget.currentTime * 1_000))))}
                onSeeked={(event) => setPlayheadMs(clampToEditable(toTimelineMs(Math.round(event.currentTarget.currentTime * 1_000))))}
                onPlay={() => setVideoPlaying(true)}
                onPause={() => {
                  setVideoPlaying(false);
                  setPreviewing(false);
                }}
                sx={{ display: "block", width: "100%", maxHeight: "52dvh", objectFit: "contain", cursor: "pointer" }}
              />
              {(pendingRender || rendering) && (
                <Box
                  sx={{
                    position: "absolute",
                    inset: 0,
                    display: "grid",
                    placeItems: "center",
                    gap: 1,
                    bgcolor: "rgba(0, 0, 0, 0.55)",
                  }}
                >
                  {pendingRender ? (
                    <Stack
                      spacing={1}
                      alignItems="center"
                      onClick={() => void fireRender()}
                      sx={{ cursor: "pointer" }}
                    >
                      <CountdownCircleTimer
                        key={renderKey}
                        isPlaying
                        duration={RENDER_DELAY_MS / 1_000}
                        colors={ACCENT_BLUE}
                        size={72}
                        strokeWidth={6}
                        onComplete={() => void fireRender()}
                      >
                        {({ remainingTime }) => (
                          <Typography sx={{ color: "common.white", fontWeight: 700 }}>
                            {remainingTime}
                          </Typography>
                        )}
                      </CountdownCircleTimer>
                      <Typography variant="caption" sx={{ color: "common.white" }}>
                        Rendering new clip in… (tap to render now)
                      </Typography>
                    </Stack>
                  ) : (
                    <Stack spacing={1} alignItems="center">
                      <CircularProgress aria-label="Rendering preview" sx={{ color: "common.white" }} />
                      <Typography variant="caption" sx={{ color: "common.white" }}>
                        Rendering preview…
                      </Typography>
                    </Stack>
                  )}
                </Box>
              )}
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
              referenceRange={referenceRange}
              selectionRange={selectionRange}
              playheadMs={playheadMs}
              stepMs={frameStepMs}
              formatRulerTime={(valueMs) => formatTimelineRulerTime(toDisplayMs(valueMs))}
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
              Length {formatTimestampMs(endMs - startMs)} · At {formatTimestampMs(toDisplayMs(playheadMs))}
            </Typography>

            <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems="center" justifyContent="center">
              {/* The [nudge, field, nudge] core is the thing that must stay
                  centered — the outer Box centers the core within the full
                  row width, and the extend button is anchored to the CORE's
                  own edge (not the outer Box's) and pushed just outside it
                  via translateX, so it protrudes right next to the nudge
                  button without ever contributing to the core's own width —
                  and so without ever affecting where the core gets centered.
                  That holds whether the Box is full-width (stacked mobile
                  layout) or shrink-wrapped (single-row desktop layout), so
                  one structure covers both breakpoints. */}
              <Box sx={{ display: "flex", justifyContent: "center", width: { xs: "100%", sm: "auto" } }}>
                <Stack direction="row" spacing={0.25} alignItems="center" sx={{ position: "relative" }}>
                  <Tooltip title={extendAvailable ? "Add 5 more seconds before Start" : "Extending isn't available for this clip"}>
                    <span style={{ position: "absolute", left: 0, top: "50%", transform: "translate(-100%, -50%)" }}>
                      <Button
                        size="small"
                        variant="text"
                        color={extendBeforeMs > 0 ? "success" : "primary"}
                        disabled={extendControlsDisabled || extendBeforeMs >= maxExtendBeforeMs}
                        onClick={() => bumpExtend("before")}
                        sx={{
                          minWidth: 0,
                          minHeight: 32,
                          p: 0,
                          gap: 0,
                          lineHeight: 1,
                        }}
                      >
                        <DoubleArrowRounded sx={{ fontSize: 32, transform: "rotate(180deg)", marginRight: "-6px" }} />
                        {extendBeforeMs > 0 ? `+${extendBeforeMs / 1_000}s` : "+5s"}
                      </Button>
                    </span>
                  </Tooltip>
                  {frameStepMs && (
                    <FrameNudgeButton
                      boundary="Start"
                      direction="backward"
                      disabled={!canNudgeBoundary("start", -1)}
                      tooltip="Move Start backward one nominal frame"
                      ariaLabel="Move Start backward one frame"
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
                        setStartInput(formatTimestampMs(toDisplayMs(startMs)));
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
                      tooltip="Move Start forward one nominal frame"
                      ariaLabel="Move Start forward one frame"
                      onClick={() => nudgeBoundaryOneFrame("start", 1)}
                      onArrowNudge={(direction) => nudgeBoundaryOneFrame("start", direction)}
                    />
                  )}
                </Stack>
              </Box>
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
              <Box sx={{ display: "flex", justifyContent: "center", width: { xs: "100%", sm: "auto" } }}>
                <Stack direction="row" spacing={0.25} alignItems="center" sx={{ position: "relative" }}>
                  {frameStepMs && (
                    <FrameNudgeButton
                      boundary="End"
                      direction="backward"
                      disabled={!canNudgeBoundary("end", -1)}
                      tooltip="Move End backward one nominal frame"
                      ariaLabel="Move End backward one frame"
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
                        setEndInput(formatTimestampMs(toDisplayMs(endMs)));
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
                      tooltip="Move End forward one nominal frame"
                      ariaLabel="Move End forward one frame"
                      onClick={() => nudgeBoundaryOneFrame("end", 1)}
                      onArrowNudge={(direction) => nudgeBoundaryOneFrame("end", direction)}
                    />
                  )}
                  <Tooltip title={extendAvailable ? "Add 5 more seconds after End" : "Extending isn't available for this clip"}>
                    <span style={{ position: "absolute", right: 0, top: "50%", transform: "translate(100%, -50%)" }}>
                      <Button
                        size="small"
                        variant="text"
                        color={extendAfterMs > 0 ? "success" : "primary"}
                        disabled={extendControlsDisabled || extendAfterMs >= maxExtendAfterMs}
                        onClick={() => bumpExtend("after")}
                        sx={{
                          minWidth: 0,
                          minHeight: 32,
                          p: 0,
                          gap: 0,
                          lineHeight: 1,
                        }}
                      >
                        {extendAfterMs > 0 ? `+${extendAfterMs / 1_000}s` : "+5s"}
                        <DoubleArrowRounded sx={{ fontSize: 32, marginLeft: "-6px" }} />
                      </Button>
                    </span>
                  </Tooltip>
                </Stack>
              </Box>
            </Stack>

            {!trimInfo.frame_rate && (
              <Alert severity="warning">
                The clip's frame rate is unavailable, so nominal one-frame nudging is disabled.
              </Alert>
            )}

            {sourceTracks.data && !sourceTracks.data.available && (
              <Alert severity="info">
                {sourceTracks.data.unavailable_reason
                  ?? "Extending this clip or changing its audio/subtitle track isn't available."}
              </Alert>
            )}

            <Stack direction="row" spacing={1} justifyContent="center">
              <Tooltip title={extendAvailable ? "Audio track" : "Not available for this clip"}>
                <span>
                  <IconButton
                    aria-label="Choose audio track"
                    disabled={extendControlsDisabled}
                    color={audioOverride !== undefined ? "success" : undefined}
                    sx={audioOverride !== undefined ? undefined : { color: ACCENT_BLUE }}
                    onClick={(event) => setAudioMenuAnchor(event.currentTarget)}
                  >
                    <LibraryMusicRounded />
                  </IconButton>
                </span>
              </Tooltip>
              <Menu anchorEl={audioMenuAnchor} open={Boolean(audioMenuAnchor)} onClose={() => setAudioMenuAnchor(null)}>
                {extendInfo?.audio_tracks.map((track) => (
                  <MenuItem
                    key={track.stream_index}
                    selected={
                      audioOverride === undefined
                        ? track.selected
                        : audioOverride !== "off" && track.stream_index === audioOverride
                    }
                    disabled={!track.available || track.stream_index === null}
                    onClick={() => track.stream_index !== null && applyAudioOverride(track.stream_index)}
                  >
                    {trackLabel(track)}
                  </MenuItem>
                ))}
                <MenuItem selected={audioOverride === "off"} onClick={() => applyAudioOverride("off")}>
                  Off
                </MenuItem>
              </Menu>

              <Tooltip title={extendAvailable ? "Subtitles" : "Not available for this clip"}>
                <span>
                  <IconButton
                    aria-label="Choose subtitle track"
                    disabled={extendControlsDisabled}
                    color={subtitleOverride !== undefined ? "success" : undefined}
                    sx={subtitleOverride !== undefined ? undefined : { color: ACCENT_BLUE }}
                    onClick={(event) => setSubtitleMenuAnchor(event.currentTarget)}
                  >
                    <SubtitlesRounded />
                  </IconButton>
                </span>
              </Tooltip>
              <Menu anchorEl={subtitleMenuAnchor} open={Boolean(subtitleMenuAnchor)} onClose={() => setSubtitleMenuAnchor(null)}>
                {/* Not highlighted for the "unchanged" default: the original
                    selection might be an external sidecar this list doesn't
                    enumerate, so there's no reliable way to tell it apart
                    from "off" without guessing. */}
                <MenuItem selected={subtitleOverride === "off"} onClick={() => applySubtitleOverride("off")}>
                  Off
                </MenuItem>
                {extendInfo?.subtitle_tracks.map((track) => (
                  <MenuItem
                    key={track.stream_index}
                    selected={
                      subtitleOverride === undefined
                        ? track.selected
                        : subtitleOverride !== "off" && track.stream_index === subtitleOverride
                    }
                    disabled={!track.available || track.stream_index === null}
                    onClick={() => track.stream_index !== null && applySubtitleOverride(track.stream_index)}
                  >
                    {trackLabel(track)}
                  </MenuItem>
                ))}
              </Menu>
            </Stack>

            {playbackError && <Alert severity="error">{playbackError}</Alert>}
            {previewRenderError && <Alert severity="error">{previewRenderError}</Alert>}
            {activeJob && (
              <Alert severity={activeJob.state === "FAILED" ? "error" : activeJob.state === "SUCCEEDED" ? "success" : "info"}>
                {activeJob.error?.message ?? activeJob.message}
                {renderDurationMs !== null &&
                  (activeJob.state === "SUCCEEDED"
                    ? ` Rendered in ${formatElapsedSeconds(renderDurationMs)}.`
                    : ` (${formatElapsedSeconds(renderDurationMs)} elapsed)`)}
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
          variant="contained"
          startIcon={<PlayArrowRounded />}
          disabled={!trimInfo || Boolean(startError || endError) || saving}
          onClick={() => void previewSelection()}
        >
          {previewing ? "Restart" : "Preview"}
        </Button>
        <Tooltip title={pendingChanges ? "To export a gif from an extended clip, save the new clip first then click export gif from the library" : ""}>
          <span>
            <Button
              variant="outlined"
              startIcon={<GifRounded />}
              disabled={gifExport.busy || endMs <= startMs || pendingChanges}
              onClick={() => gifExport.exportGif({ startMs, endMs })}
            >
              {gifExport.busy ? "Exporting GIF…" : "Export GIF"}
            </Button>
          </span>
        </Tooltip>
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
    <Dialog
      open={confirmingReset}
      onClose={() => setConfirmingReset(false)}
      maxWidth="sm"
      fullWidth
      aria-labelledby="confirm-reset-title"
    >
      <DialogTitle id="confirm-reset-title">Discard the modified preview?</DialogTitle>
      <DialogContent>
        <Alert severity="warning">
          Resetting will discard the extended range, track changes, and the rendered preview.
        </Alert>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => setConfirmingReset(false)}>Nevermind</Button>
        <Button
          color="error"
          variant="contained"
          onClick={() => {
            setConfirmingReset(false);
            performReset();
          }}
        >
          Reset
        </Button>
      </DialogActions>
    </Dialog>
    </>
  );
}
