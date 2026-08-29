import AddRounded from "@mui/icons-material/AddRounded";
import ArrowDownwardRounded from "@mui/icons-material/ArrowDownwardRounded";
import ArrowUpwardRounded from "@mui/icons-material/ArrowUpwardRounded";
import AvTimerRounded from "@mui/icons-material/AvTimerRounded";
import CheckCircleRounded from "@mui/icons-material/CheckCircleRounded";
import ContentCutRounded from "@mui/icons-material/ContentCutRounded";
import DeleteOutlineRounded from "@mui/icons-material/DeleteOutlineRounded";
import ErrorRounded from "@mui/icons-material/ErrorRounded";
import MovieRounded from "@mui/icons-material/MovieRounded";
import PersonRounded from "@mui/icons-material/PersonRounded";
import PlayArrowRounded from "@mui/icons-material/PlayArrowRounded";
import RemoveRounded from "@mui/icons-material/RemoveRounded";
import RestartAltRounded from "@mui/icons-material/RestartAltRounded";
import SettingsRounded from "@mui/icons-material/SettingsRounded";
import SmartDisplayRounded from "@mui/icons-material/SmartDisplayRounded";
import WarningRounded from "@mui/icons-material/WarningRounded";
import {
  Alert,
  AppBar,
  Autocomplete,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Container,
  CssBaseline,
  Divider,
  FormControl,
  IconButton,
  InputLabel,
  LinearProgress,
  List,
  ListItemButton,
  ListItem,
  ListItemIcon,
  ListItemText,
  MenuItem,
  Select,
  Stack,
  TextField,
  ThemeProvider,
  Toolbar,
  Typography,
  createTheme,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";

import {
  createClip,
  fetchMediaCapabilities,
  fetchJob,
  fetchHealth,
  fetchPlexSessions,
  fetchSettings,
  testPlexConnection,
  updateSettings,
} from "./api";
import { formatTimestampMs, parseTimestampMs } from "./timestamps";
import type {
  ApplicationSettingField,
  ApplicationSettings,
  ApplicationSettingsUpdate,
  ClipCreateRequest,
  HealthStatus,
  JobSnapshot,
  JobState,
  MediaCapabilities,
  PlexConnectionRequest,
  PlexConnectionResult,
  PlexSession,
  PlexSessionSnapshot,
  PlexSessionSnapshotStatus,
  SourcePathMapping,
  TrackDescriptor,
} from "./types";

const theme = createTheme({
  palette: {
    mode: "dark",
    primary: { main: "#60a5fa" },
    background: { default: "#0b1120", paper: "#111827" },
  },
  shape: { borderRadius: 8 },
});

const x264Presets = [
  "ultrafast",
  "superfast",
  "veryfast",
  "faster",
  "fast",
  "medium",
  "slow",
  "slower",
  "veryslow",
];
const PLEX_TOKEN_MASK = "●●●●●●●●";

const sessionStatusSeverity: Record<
  PlexSessionSnapshotStatus,
  "success" | "info" | "warning" | "error"
> = {
  ok: "success",
  not_configured: "info",
  invalid_url: "error",
  invalid_token: "error",
  http_error: "warning",
  invalid_response: "error",
  unreachable: "warning",
  error: "error",
};

function tokenDraft(settings: ApplicationSettings): string {
  return settings.plex_token_configured ? PLEX_TOKEN_MASK : "";
}

function enteredToken(value: string): string {
  const token = value.trim();
  return token === PLEX_TOKEN_MASK ? "" : token;
}

function StatusIcon({ status }: { status: HealthStatus }) {
  if (status === "ok") return <CheckCircleRounded color="success" />;
  if (status === "degraded") return <WarningRounded color="warning" />;
  return <ErrorRounded color="error" />;
}

function StatusChip({ status }: { status: HealthStatus }) {
  const color = status === "ok" ? "success" : status === "degraded" ? "warning" : "error";
  return <Chip label={status.toUpperCase()} color={color} size="small" />;
}

function ManagedLabel({ managed }: { managed: boolean }) {
  return managed ? <Chip label="Environment managed" size="small" variant="outlined" /> : null;
}

function formatMilliseconds(value: number | null): string {
  return formatTimestampMs(value) || "--:--";
}

function useClock(enabled: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [enabled]);
  return now;
}

function displayedPosition(session: PlexSession, now: number): number {
  if (session.state.toLowerCase() !== "playing") return session.position_ms;
  const sampledAt = Date.parse(session.sampled_at);
  if (!Number.isFinite(sampledAt)) return session.position_ms;
  const extrapolated = session.position_ms + Math.max(0, now - sampledAt);
  return session.duration_ms === null ? extrapolated : Math.min(session.duration_ms, extrapolated);
}

function useLivePlexSessions() {
  const queryClient = useQueryClient();
  const sessions = useQuery({
    queryKey: ["plex-sessions"],
    queryFn: fetchPlexSessions,
    refetchInterval: 15_000,
  });

  useEffect(() => {
    if (typeof EventSource === "undefined") return undefined;
    const eventSource = new EventSource("/api/sessions/events");
    const handleSnapshot = (event: MessageEvent<string>) => {
      const snapshot = JSON.parse(event.data) as PlexSessionSnapshot;
      queryClient.setQueryData(["plex-sessions"], snapshot);
    };
    const handleError = () => {
      void queryClient.invalidateQueries({ queryKey: ["plex-sessions"] });
    };
    eventSource.addEventListener("snapshot", handleSnapshot as EventListener);
    eventSource.addEventListener("error", handleError);
    return () => {
      eventSource.removeEventListener("snapshot", handleSnapshot as EventListener);
      eventSource.removeEventListener("error", handleError);
      eventSource.close();
    };
  }, [queryClient]);

  return sessions;
}

function useJobSnapshot(initialJob: JobSnapshot | null) {
  const [job, setJob] = useState<JobSnapshot | null>(initialJob);

  useEffect(() => {
    setJob(initialJob);
    if (!initialJob || typeof EventSource === "undefined") return undefined;

    let closed = false;
    const eventSource = new EventSource(`/api/jobs/${encodeURIComponent(initialJob.id)}/events`);
    const handleSnapshot = (event: MessageEvent<string>) => {
      setJob(JSON.parse(event.data) as JobSnapshot);
    };
    const handleError = () => {
      if (!closed) {
        void fetchJob(initialJob.id)
          .then((snapshot) => {
            if (!closed && snapshot.id === initialJob.id) setJob(snapshot);
          })
          .catch(() => undefined);
      }
    };

    eventSource.addEventListener("snapshot", handleSnapshot as EventListener);
    eventSource.addEventListener("error", handleError);
    return () => {
      closed = true;
      eventSource.removeEventListener("snapshot", handleSnapshot as EventListener);
      eventSource.removeEventListener("error", handleError);
      eventSource.close();
    };
  }, [initialJob]);

  return job;
}

function jobSeverity(state: JobState): "success" | "info" | "warning" | "error" {
  if (state === "SUCCEEDED") return "success";
  if (state === "PARTIAL") return "warning";
  if (state === "FAILED") return "error";
  return "info";
}

function SessionDetail({ session }: { session: PlexSession }) {
  const now = useClock(session.state.toLowerCase() === "playing");
  const position = displayedPosition(session, now);
  const progress =
    session.duration_ms && session.duration_ms > 0
      ? Math.min(100, Math.max(0, (position / session.duration_ms) * 100))
      : 0;
  return (
    <Stack spacing={2}>
      <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
        <Chip
          icon={<PlayArrowRounded />}
          label={session.state}
          color={session.state.toLowerCase() === "playing" ? "success" : "default"}
          sx={{ alignSelf: "flex-start", textTransform: "capitalize" }}
        />
        <Chip
          icon={<MovieRounded />}
          label={session.media_type}
          variant="outlined"
          sx={{ alignSelf: "flex-start", textTransform: "capitalize" }}
        />
        {session.plex_user && (
          <Chip icon={<PersonRounded />} label={session.plex_user} variant="outlined" />
        )}
        {session.player && (
          <Chip icon={<SmartDisplayRounded />} label={session.player} variant="outlined" />
        )}
      </Stack>
      <Box>
        <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
          <Typography variant="body2" color="text.secondary">Playback position</Typography>
          <Typography variant="body2" sx={{ fontVariantNumeric: "tabular-nums" }}>
            {formatMilliseconds(position)} / {formatMilliseconds(session.duration_ms)}
          </Typography>
        </Stack>
        <LinearProgress variant="determinate" value={progress} aria-label="Playback progress" />
      </Box>
    </Stack>
  );
}

function trackLabel(track: TrackDescriptor): string {
  const parts = [
    track.language?.toUpperCase(),
    track.title,
    track.codec,
    track.stream_index === null ? null : `#${track.stream_index}`,
  ].filter(Boolean);
  return parts.join(" · ") || "Unnamed track";
}

function selectedTrackIndex(tracks: TrackDescriptor[], fallback: number | null): number | "" {
  const selected = tracks.find((track) => track.selected && track.stream_index !== null);
  return selected?.stream_index ?? fallback ?? "";
}

function MediaTrackSelectors({
  capabilities,
  audioStreamIndex,
  subtitleStreamIndex,
  subtitlesEnabled,
  onAudioChange,
  onSubtitleChange,
}: {
  capabilities: MediaCapabilities | undefined;
  audioStreamIndex: number | "";
  subtitleStreamIndex: number | "";
  subtitlesEnabled: boolean;
  onAudioChange: (value: number | "") => void;
  onSubtitleChange: (enabled: boolean, value: number | "") => void;
}) {
  if (!capabilities) return null;
  const subtitleOptions = capabilities.subtitle_tracks.filter((track) => track.stream_index !== null);
  return (
    <Stack spacing={2}>
      <Typography variant="h6">Tracks</Typography>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
        <FormControl fullWidth>
          <InputLabel id="audio-track-label">Audio</InputLabel>
          <Select
            labelId="audio-track-label"
            label="Audio"
            value={audioStreamIndex}
            onChange={(event) => onAudioChange(event.target.value as number | "")}
          >
            {capabilities.audio_tracks.map((track) => (
              <MenuItem
                key={track.stream_index}
                value={track.stream_index ?? ""}
                disabled={!track.available}
              >
                {trackLabel(track)}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <FormControl fullWidth>
          <InputLabel id="subtitle-track-label">Subtitles</InputLabel>
          <Select
            labelId="subtitle-track-label"
            label="Subtitles"
            value={subtitlesEnabled ? subtitleStreamIndex : ""}
            onChange={(event) => {
              const value = event.target.value as number | "";
              onSubtitleChange(value !== "", value);
            }}
          >
            <MenuItem value="">Off</MenuItem>
            {subtitleOptions.map((track) => (
              <MenuItem
                key={track.stream_index}
                value={track.stream_index ?? ""}
                disabled={!track.available}
              >
                {trackLabel(track)}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Stack>
      {capabilities.subtitle_tracks.some((track) => !track.available) && (
        <Alert severity="warning">
          Some subtitle tracks are unavailable for burning. Choose a listed available track or Off.
        </Alert>
      )}
      {(capabilities.hdr.hdr10 || capabilities.hdr.hlg) && (
        <Alert severity="info">HDR source detected. SDR tone mapping is handled in a later phase.</Alert>
      )}
    </Stack>
  );
}

type Boundary = "start" | "end";
type AppPage = "make-clip" | "settings";

const MAX_ADJUSTMENT_SECONDS = 99;

function adjustmentLabel(seconds: number): string {
  return seconds > 0 ? `+${seconds}` : seconds.toString();
}

function clampAdjustmentSeconds(seconds: number): number {
  return Math.min(MAX_ADJUSTMENT_SECONDS, Math.max(-MAX_ADJUSTMENT_SECONDS, seconds));
}

function clampBoundaryMs(value: number, maximumMs: number | null): number {
  const nonNegative = Math.max(0, Math.floor(value));
  return maximumMs === null ? nonNegative : Math.min(nonNegative, maximumMs);
}

function mediaCapabilitiesVersion(session: PlexSession | undefined): string {
  if (!session) return "";
  const streamSelection = (streams: PlexSession["selected_audio_streams"]): string[] =>
    streams
      .map((stream) => [stream.id, stream.key, stream.stream_index, stream.codec].join("/"))
      .sort();
  return JSON.stringify({
    mediaIdentity: session.media_identity,
    partId: session.plex_part_id,
    partKey: session.plex_part_key,
    audio: streamSelection(session.selected_audio_streams),
    subtitles: streamSelection(session.selected_subtitle_streams),
  });
}

function MakeClipScreen() {
  const sessions = useLivePlexSessions();
  const [selectedSessionIdentity, setSelectedSessionIdentity] = useState<string | null>(null);
  const [selectedMediaIdentity, setSelectedMediaIdentity] = useState<string | null>(null);
  const [startMs, setStartMs] = useState<number | null>(null);
  const [endMs, setEndMs] = useState<number | null>(null);
  const [startInput, setStartInput] = useState("");
  const [endInput, setEndInput] = useState("");
  const [adjustmentSeconds, setAdjustmentSeconds] = useState(5);
  const [boundaryNotice, setBoundaryNotice] = useState<string | null>(null);
  const [audioStreamIndex, setAudioStreamIndex] = useState<number | "">("");
  const [subtitleStreamIndex, setSubtitleStreamIndex] = useState<number | "">("");
  const [subtitlesEnabled, setSubtitlesEnabled] = useState(false);
  const [submittedJob, setSubmittedJob] = useState<JobSnapshot | null>(null);
  const snapshot = sessions.data;
  const selectedSession = snapshot?.sessions.find(
    (session) => session.session_identity === selectedSessionIdentity,
  );
  const now = useClock(selectedSession?.state.toLowerCase() === "playing");
  const livePositionMs = selectedSession ? displayedPosition(selectedSession, now) : null;
  const selectedSessionEnded = Boolean(selectedSessionIdentity && snapshot && !selectedSession);
  const capabilitiesVersion = mediaCapabilitiesVersion(selectedSession);
  const startParse = parseTimestampMs(startInput);
  const endParse = parseTimestampMs(endInput);
  const activeJob = useJobSnapshot(submittedJob);
  const capabilities = useQuery({
    queryKey: ["media-capabilities", selectedSessionIdentity, capabilitiesVersion],
    queryFn: () => fetchMediaCapabilities(selectedSessionIdentity || ""),
    enabled: Boolean(selectedSessionIdentity && selectedSession),
  });
  const clipCreate = useMutation({
    mutationFn: createClip,
    onSuccess: (job) => setSubmittedJob(job),
  });

  const setBoundary = (boundary: Boundary, value: number | null) => {
    const nextInput = formatTimestampMs(value);
    if (boundary === "start") {
      setStartMs(value);
      setStartInput(nextInput);
    } else {
      setEndMs(value);
      setEndInput(nextInput);
    }
    setBoundaryNotice(null);
    setSubmittedJob(null);
    clipCreate.reset();
  };

  const handleBoundaryInput = (boundary: Boundary, value: string) => {
    const parsed = parseTimestampMs(value);
    if (boundary === "start") {
      setStartInput(value);
      setStartMs(parsed.error ? null : parsed.value);
    } else {
      setEndInput(value);
      setEndMs(parsed.error ? null : parsed.value);
    }
    setBoundaryNotice(null);
    setSubmittedJob(null);
    clipCreate.reset();
  };

  useEffect(() => {
    if (
      !selectedSession ||
      !selectedMediaIdentity ||
      selectedSession.media_identity === selectedMediaIdentity
    ) {
      return;
    }
    setSelectedMediaIdentity(selectedSession.media_identity);
    setBoundary("start", null);
    setBoundary("end", null);
    setBoundaryNotice("The selected player changed media, so captured boundaries were cleared.");
  }, [selectedMediaIdentity, selectedSession]);

  useEffect(() => {
    if (!capabilities.data) return;
    const nextAudio = selectedTrackIndex(
      capabilities.data.audio_tracks,
      capabilities.data.default_audio_stream_index,
    );
    const nextSubtitle = selectedTrackIndex(
      capabilities.data.subtitle_tracks,
      capabilities.data.default_subtitle_stream_index,
    );
    setAudioStreamIndex(nextAudio);
    setSubtitleStreamIndex(nextSubtitle);
    setSubtitlesEnabled(nextSubtitle !== "");
  }, [capabilities.data]);

  const rangeError =
    startParse.error ??
    endParse.error ??
    (selectedSession && startMs === null ? "Capture Start before creating a clip." : null) ??
    (selectedSession && endMs === null ? "Capture End before creating a clip." : null) ??
    (startMs !== null && endMs !== null && endMs <= startMs
      ? "End must be later than Start."
      : null) ??
    (selectedSession?.duration_ms !== null &&
    selectedSession?.duration_ms !== undefined &&
    endMs !== null &&
    endMs > selectedSession.duration_ms
      ? "End must be within the selected media duration."
      : null);

  const submitClip = () => {
    if (!selectedSession || !selectedMediaIdentity || startMs === null || endMs === null) return;
    const request: ClipCreateRequest = {
      session_identity: selectedSession.session_identity,
      media_identity: selectedMediaIdentity,
      start_ms: startMs,
      end_ms: endMs,
      audio_stream_index: audioStreamIndex === "" ? null : Number(audioStreamIndex),
      subtitle_stream_index:
        subtitlesEnabled && subtitleStreamIndex !== "" ? Number(subtitleStreamIndex) : null,
      subtitles_enabled: subtitlesEnabled,
    };
    clipCreate.mutate(request);
  };

  const adjustStart = () => {
    if (!selectedSession || startMs === null) return;
    setBoundary(
      "start",
      clampBoundaryMs(startMs + adjustmentSeconds * 1_000, selectedSession.duration_ms),
    );
  };

  const adjustEnd = () => {
    const baseMs = endMs ?? startMs;
    if (!selectedSession || baseMs === null) return;
    setBoundary(
      "end",
      clampBoundaryMs(baseMs + adjustmentSeconds * 1_000, selectedSession.duration_ms),
    );
  };

  const changeAdjustmentSeconds = (change: number) => {
    setAdjustmentSeconds((current) => clampAdjustmentSeconds(current + change));
  };

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack spacing={3}>
          <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" spacing={2}>
            <Box>
              <Typography variant="h5" gutterBottom>Make Clip</Typography>
              <Typography color="text.secondary">Live Plex video sessions</Typography>
            </Box>
            {sessions.isFetching && <CircularProgress size={24} aria-label="Refreshing sessions" />}
          </Stack>

          {sessions.error && <Alert severity="error">{sessions.error.message}</Alert>}
          {snapshot && (
            <Alert severity={sessionStatusSeverity[snapshot.status]}>
              {snapshot.message}
            </Alert>
          )}
          {selectedSessionEnded && (
            <Alert severity="warning">The selected Plex session ended.</Alert>
          )}
          {boundaryNotice && <Alert severity="info">{boundaryNotice}</Alert>}

          {snapshot && snapshot.sessions.length > 0 ? (
            <List disablePadding>
              {snapshot.sessions.map((session) => {
                const selected = session.session_identity === selectedSessionIdentity;
                return (
                  <ListItemButton
                    key={session.session_identity}
                    selected={selected}
                    onClick={() => {
                      if (selected) return;
                      setSelectedSessionIdentity(session.session_identity);
                      setSelectedMediaIdentity(session.media_identity);
                      setBoundary("start", displayedPosition(session, Date.now()));
                      setBoundary("end", null);
                      setBoundaryNotice(null);
                      setAudioStreamIndex("");
                      setSubtitleStreamIndex("");
                      setSubtitlesEnabled(false);
                    }}
                    sx={{ borderRadius: 1, mb: 1, alignItems: "flex-start" }}
                  >
                    <ListItemIcon sx={{ minWidth: 42, pt: 0.5 }}>
                      <AvTimerRounded color={selected ? "primary" : "inherit"} />
                    </ListItemIcon>
                    <ListItemText
                      primary={session.title}
                      secondary={<SessionDetail session={session} />}
                      slotProps={{ secondary: { component: "div" } }}
                    />
                  </ListItemButton>
                );
              })}
            </List>
          ) : (
            <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 3 }}>
              <Typography color="text.secondary">No active sessions.</Typography>
            </Box>
          )}

          {selectedSession && (
            <>
              <Divider />
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

                {capabilities.isFetching && (
                  <Stack direction="row" spacing={1} alignItems="center">
                    <CircularProgress size={18} aria-label="Loading media capabilities" />
                    <Typography color="text.secondary">Loading media tracks…</Typography>
                  </Stack>
                )}
                {capabilities.error && (
                  <Alert severity="error">{capabilities.error.message}</Alert>
                )}
                <MediaTrackSelectors
                  capabilities={capabilities.data}
                  audioStreamIndex={audioStreamIndex}
                  subtitleStreamIndex={subtitleStreamIndex}
                  subtitlesEnabled={subtitlesEnabled}
                  onAudioChange={(value) => {
                    setAudioStreamIndex(value);
                    setSubmittedJob(null);
                    clipCreate.reset();
                  }}
                  onSubtitleChange={(enabled, value) => {
                    setSubtitlesEnabled(enabled);
                    setSubtitleStreamIndex(value);
                    setSubmittedJob(null);
                    clipCreate.reset();
                  }}
                />

                <Stack direction="row" spacing={2} useFlexGap flexWrap="wrap" alignItems="flex-start">
                  <Stack direction="row" spacing={1} alignItems="flex-start">
                    <TextField
                      label="Start"
                      placeholder="00:00:00.000"
                      value={startInput}
                      error={Boolean(startParse.error)}
                      helperText={startParse.error ?? "HH:MM:SS.mmm"}
                      onChange={(event) => handleBoundaryInput("start", event.target.value)}
                      sx={{ width: { xs: "15ch", sm: "16ch" } }}
                    />
                    <Button
                      aria-label="Set Start to current position"
                      startIcon={<AvTimerRounded />}
                      variant="outlined"
                      disabled={livePositionMs === null}
                      onClick={() => setBoundary("start", livePositionMs)}
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
                      onChange={(event) => handleBoundaryInput("end", event.target.value)}
                      sx={{ width: { xs: "15ch", sm: "16ch" } }}
                    />
                    <Button
                      aria-label="Set End to current position"
                      startIcon={<AvTimerRounded />}
                      variant="outlined"
                      disabled={livePositionMs === null}
                      onClick={() => setBoundary("end", livePositionMs)}
                      sx={{ minHeight: 56 }}
                    >
                      Set
                    </Button>
                  </Stack>
                  <Button
                    startIcon={<RestartAltRounded />}
                    variant="outlined"
                    onClick={() => {
                      setBoundary("start", null);
                      setBoundary("end", null);
                    }}
                    sx={{ minHeight: 56 }}
                  >
                    Clear
                  </Button>
                </Stack>

                <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" alignItems="flex-start">
                  <TextField
                    type="number"
                    label="Seconds"
                    value={adjustmentSeconds}
                    onWheelCapture={(event) => {
                      if (event.deltaY === 0) return;
                      event.preventDefault();
                      changeAdjustmentSeconds(event.deltaY < 0 ? 1 : -1);
                    }}
                    onChange={(event) => {
                      const value = Number(event.target.value);
                      setAdjustmentSeconds(
                        Number.isFinite(value) ? clampAdjustmentSeconds(Math.trunc(value)) : 0,
                      );
                    }}
                    slotProps={{
                      htmlInput: {
                        max: MAX_ADJUSTMENT_SECONDS,
                        min: -MAX_ADJUSTMENT_SECONDS,
                        step: 1,
                      },
                    }}
                    sx={{ width: "12ch" }}
                  />
                  <Stack direction="row" spacing={0.5} sx={{ minHeight: 56 }}>
                    <IconButton
                      aria-label="Decrease seconds"
                      onClick={() => changeAdjustmentSeconds(-1)}
                      sx={{ border: 1, borderColor: "divider", borderRadius: 1, minHeight: 56 }}
                    >
                      <RemoveRounded />
                    </IconButton>
                    <IconButton
                      aria-label="Increase seconds"
                      onClick={() => changeAdjustmentSeconds(1)}
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
                {clipCreate.error && <Alert severity="error">{clipCreate.error.message}</Alert>}
                {activeJob && (
                  <Stack spacing={2}>
                    <Alert severity={jobSeverity(activeJob.state)}>
                      {activeJob.message}
                      {activeJob.queue_position ? ` Queue position ${activeJob.queue_position}.` : ""}
                      {activeJob.error ? ` ${activeJob.error.message}` : ""}
                    </Alert>
                    {activeJob.state !== "SUCCEEDED" && activeJob.state !== "FAILED" && (
                      <LinearProgress
                        variant="determinate"
                        value={Math.round(activeJob.progress * 100)}
                        aria-label="Clip render progress"
                      />
                    )}
                    {activeJob.state === "SUCCEEDED" && activeJob.result && (
                      <Stack spacing={2}>
                        <Box
                          component="video"
                          src={activeJob.result.play_url}
                          controls
                          sx={{ width: "100%", borderRadius: 1, bgcolor: "black" }}
                        />
                        <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
                          <Chip label={activeJob.result.title} color="success" variant="outlined" />
                          <Chip
                            label={formatMilliseconds(activeJob.result.duration_ms)}
                            variant="outlined"
                          />
                          <Button
                            href={activeJob.result.download_url}
                            variant="outlined"
                            startIcon={<ArrowDownwardRounded />}
                          >
                            Download
                          </Button>
                        </Stack>
                      </Stack>
                    )}
                  </Stack>
                )}

                <Button
                  startIcon={<ContentCutRounded />}
                  variant="contained"
                  disabled={Boolean(rangeError) || clipCreate.isPending}
                  onClick={submitClip}
                  sx={{ alignSelf: "flex-start" }}
                >
                  {clipCreate.isPending ? "Submitting…" : "Create clip"}
                </Button>
              </Stack>
            </>
          )}
        </Stack>
      </CardContent>
    </Card>
  );
}

function initialTimezone(settings: ApplicationSettings): string {
  if (settings.timezone_configured) return settings.timezone;
  const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return detected && settings.available_timezones.includes(detected)
    ? detected
    : settings.timezone;
}

interface PlexCandidate {
  test: PlexConnectionRequest;
  save: ApplicationSettingsUpdate;
}

interface SettingsOperation {
  kind: "save" | "test";
  baseUpdate: ApplicationSettingsUpdate;
  plexCandidate?: PlexCandidate;
}

function SettingsForm({ settings }: { settings: ApplicationSettings }) {
  const queryClient = useQueryClient();
  const [plexUrl, setPlexUrl] = useState(settings.plex_url);
  const [plexToken, setPlexToken] = useState(() => tokenDraft(settings));
  const [timezone, setTimezone] = useState(() => initialTimezone(settings));
  const [x264Preset, setX264Preset] = useState(settings.x264_preset);
  const [mappings, setMappings] = useState<SourcePathMapping[]>(settings.source_path_mappings);
  const [connection, setConnection] = useState<PlexConnectionResult | null>(null);

  useEffect(() => {
    setPlexUrl(settings.plex_url);
  }, [settings.plex_url]);

  useEffect(() => {
    setPlexToken(tokenDraft(settings));
    setTimezone(initialTimezone(settings));
    setX264Preset(settings.x264_preset);
    setMappings(settings.source_path_mappings);
  }, [settings]);

  const managed = (field: ApplicationSettingField) => settings.environment_managed[field];
  const save = useMutation({
    mutationFn: async (operation: SettingsOperation) => {
      let updated = Object.keys(operation.baseUpdate).length
        ? await updateSettings(operation.baseUpdate)
        : settings;
      if (!operation.plexCandidate) {
        return {
          settings: updated,
          connection: null,
          notice: "Settings saved.",
        };
      }

      const result = await testPlexConnection(operation.plexCandidate.test);
      if (!result.connected) {
        return {
          settings: updated,
          connection: result,
          notice:
            operation.kind === "test"
              ? "Connection failed. Plex settings were not saved."
              : "Other settings were saved. The new Plex credentials were rejected.",
        };
      }

      if (Object.keys(operation.plexCandidate.save).length) {
        updated = await updateSettings(operation.plexCandidate.save);
      }
      return {
        settings: updated,
        connection: result,
        notice:
          operation.kind === "test"
            ? "Connection succeeded and Plex settings were saved."
            : "Settings and verified Plex credentials were saved.",
      };
    },
    onSuccess: (result) => {
      queryClient.setQueryData(["settings"], result.settings);
      setConnection(result.connection);
      setPlexToken(tokenDraft(result.settings));
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const submittedToken = enteredToken(plexToken);
    const baseUpdate: ApplicationSettingsUpdate = {
      ...(!managed("source_path_mappings") && { source_path_mappings: mappings }),
      ...(!managed("timezone") && { timezone }),
      ...(!managed("x264_preset") && { x264_preset: x264Preset }),
    };
    save.mutate({
      kind: "save",
      baseUpdate,
      ...(!managed("plex_token") &&
        submittedToken && {
          plexCandidate: {
            test: { plex_url: plexUrl, plex_token: submittedToken },
            save: {
              ...(!managed("plex_url") && { plex_url: plexUrl }),
              plex_token: submittedToken,
            },
          },
        }),
    });
  };
  const testCurrentConnection = () => {
    const submittedToken = enteredToken(plexToken);
    if (!submittedToken && plexUrl !== settings.plex_url) {
      save.reset();
      setConnection({
        connected: false,
        code: "PLEX_CREDENTIALS_REQUIRED",
        message: "Enter the Plex token when testing a different server URL.",
        server_name: null,
        server_version: null,
      });
      return;
    }
    const candidate = submittedToken
      ? { plex_url: plexUrl, plex_token: submittedToken }
      : {};
    save.mutate({
      kind: "test",
      baseUpdate: {},
      plexCandidate: {
        test: candidate,
        save: {
          ...(!managed("plex_url") && submittedToken && { plex_url: plexUrl }),
          ...(!managed("plex_token") && submittedToken && { plex_token: submittedToken }),
        },
      },
    });
  };

  const changeMapping = (index: number, field: keyof SourcePathMapping, value: string) => {
    setMappings((current) =>
      current.map((mapping, mappingIndex) =>
        mappingIndex === index ? { ...mapping, [field]: value } : mapping,
      ),
    );
  };
  const moveMapping = (index: number, offset: -1 | 1) => {
    setMappings((current) => {
      const destination = index + offset;
      if (destination < 0 || destination >= current.length) return current;
      const reordered = [...current];
      [reordered[index], reordered[destination]] = [reordered[destination], reordered[index]];
      return reordered;
    });
  };

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack component="form" spacing={3} onSubmit={submit}>
          <Box>
            <Typography variant="h5" gutterBottom>Plex connection</Typography>
            <Typography color="text.secondary">
              Credentials stay on the server. The API only reports whether a token is configured.
            </Typography>
          </Box>

          <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
            <TextField
              fullWidth
              label="Plex server URL"
              placeholder="http://192.168.1.20:32400"
              value={plexUrl}
              disabled={managed("plex_url")}
              onChange={(event) => {
                setPlexUrl(event.target.value);
                setConnection(null);
              }}
            />
            <ManagedLabel managed={managed("plex_url")} />
          </Stack>

          <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
            <TextField
              fullWidth
              type="password"
              name="plex_token"
              label="Plex token"
              placeholder="Enter token"
              value={plexToken}
              slotProps={{ inputLabel: { shrink: true } }}
              disabled={managed("plex_token")}
              onFocus={() => {
                if (plexToken === PLEX_TOKEN_MASK) setPlexToken("");
              }}
              onBlur={() => {
                if (!plexToken && settings.plex_token_configured) setPlexToken(PLEX_TOKEN_MASK);
              }}
              onChange={(event) => {
                setPlexToken(event.target.value);
                setConnection(null);
              }}
              helperText="The saved token is replaced only when you enter a new one."
            />
            <ManagedLabel managed={managed("plex_token")} />
          </Stack>

          <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "center" }}>
            <Button
              variant="outlined"
              disabled={!plexUrl.trim() || save.isPending}
              onClick={testCurrentConnection}
            >
              {save.isPending && save.variables?.kind === "test" ? "Testing…" : "Test connection"}
            </Button>
            <Typography color="text.secondary" variant="body2">
              Tests the current URL/token and saves them only when the connection succeeds.
            </Typography>
          </Stack>
          {connection && (
            <Alert severity={connection.connected ? "success" : "error"}>
              {connection.message}{connection.server_name ? ` Server: ${connection.server_name}.` : ""}
            </Alert>
          )}

          <Divider />

          <Box>
            <Stack direction="row" spacing={1} alignItems="center" mb={1}>
              <Typography variant="h5">Source-path mappings</Typography>
              <ManagedLabel managed={managed("source_path_mappings")} />
            </Stack>
            <Typography color="text.secondary">
              Mappings are checked from top to bottom. Example: Plex prefix <strong>D:\Media\Movies</strong>{" "}
              maps to local/container prefix <strong>/media/movies</strong>.
            </Typography>
          </Box>

          {mappings.map((mapping, index) => (
            <Stack
              key={index}
              direction={{ xs: "column", md: "row" }}
              spacing={1}
              alignItems={{ md: "center" }}
            >
              <TextField
                fullWidth
                label={`Plex prefix ${index + 1}`}
                placeholder="D:\Media or /srv/plex/media"
                value={mapping.plex_prefix}
                disabled={managed("source_path_mappings")}
                onChange={(event) => changeMapping(index, "plex_prefix", event.target.value)}
              />
              <TextField
                fullWidth
                label={`Local/container prefix ${index + 1}`}
                placeholder="/media"
                value={mapping.local_prefix}
                disabled={managed("source_path_mappings")}
                onChange={(event) => changeMapping(index, "local_prefix", event.target.value)}
              />
              <IconButton
                aria-label={`Move mapping ${index + 1} up`}
                disabled={managed("source_path_mappings") || index === 0}
                onClick={() => moveMapping(index, -1)}
              >
                <ArrowUpwardRounded />
              </IconButton>
              <IconButton
                aria-label={`Move mapping ${index + 1} down`}
                disabled={managed("source_path_mappings") || index === mappings.length - 1}
                onClick={() => moveMapping(index, 1)}
              >
                <ArrowDownwardRounded />
              </IconButton>
              <IconButton
                aria-label={`Remove mapping ${index + 1}`}
                disabled={managed("source_path_mappings")}
                onClick={() => setMappings((current) => current.filter((_, item) => item !== index))}
              >
                <DeleteOutlineRounded />
              </IconButton>
            </Stack>
          ))}
          <Button
            startIcon={<AddRounded />}
            disabled={managed("source_path_mappings")}
            onClick={() => setMappings((current) => [...current, { plex_prefix: "", local_prefix: "" }])}
            sx={{ alignSelf: "flex-start" }}
          >
            Add mapping
          </Button>

          <Divider />

          <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
            <Stack direction="row" spacing={1} alignItems="center" flex={1}>
              <Autocomplete
                fullWidth
                disableClearable
                options={settings.available_timezones}
                value={timezone}
                disabled={managed("timezone")}
                onChange={(_event, value) => setTimezone(value)}
                renderInput={(parameters) => (
                  <TextField
                    {...parameters}
                    label="Timezone"
                    helperText={
                      settings.timezone_configured
                        ? "IANA timezone used for application timestamps."
                        : "Detected from this browser. Save settings to keep it."
                    }
                  />
                )}
              />
              <ManagedLabel managed={managed("timezone")} />
            </Stack>
            <Stack direction="row" spacing={1} alignItems="center" flex={1}>
              <FormControl fullWidth disabled={managed("x264_preset")}>
                <InputLabel id="x264-preset-label">x264 preset</InputLabel>
                <Select
                  labelId="x264-preset-label"
                  label="x264 preset"
                  value={x264Preset}
                  onChange={(event) => setX264Preset(event.target.value)}
                >
                  {x264Presets.map((preset) => <MenuItem key={preset} value={preset}>{preset}</MenuItem>)}
                </Select>
              </FormControl>
              <ManagedLabel managed={managed("x264_preset")} />
            </Stack>
          </Stack>

          {save.error && <Alert severity="error">{save.error.message}</Alert>}
          {save.data && (
            <Alert severity={save.data.connection && !save.data.connection.connected ? "warning" : "success"}>
              {save.data.notice}
            </Alert>
          )}
          <Button type="submit" variant="contained" disabled={save.isPending} sx={{ alignSelf: "flex-start" }}>
            {save.isPending && save.variables?.kind === "save" ? "Saving…" : "Save settings"}
          </Button>
        </Stack>
      </CardContent>
    </Card>
  );
}

export function App() {
  const [page, setPage] = useState<AppPage>("make-clip");
  const health = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 10_000,
  });
  const settings = useQuery({ queryKey: ["settings"], queryFn: fetchSettings });

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <AppBar position="static" color="transparent" elevation={0}>
        <Toolbar sx={{ gap: 1, flexWrap: "wrap", py: 1 }}>
          <ContentCutRounded sx={{ mr: 1 }} />
          <Typography variant="h6" sx={{ flexGrow: 1, fontWeight: 700, minWidth: 180 }}>
            MediaClipMakarr
          </Typography>
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mr: { sm: 2 } }}>
            <Button
              color={page === "make-clip" ? "primary" : "inherit"}
              variant={page === "make-clip" ? "outlined" : "text"}
              onClick={() => setPage("make-clip")}
            >
              Make Clip
            </Button>
            <Button
              color={page === "settings" ? "primary" : "inherit"}
              startIcon={<SettingsRounded />}
              variant={page === "settings" ? "outlined" : "text"}
              onClick={() => setPage("settings")}
            >
              Settings
            </Button>
          </Stack>
          {health.data && <StatusChip status={health.data.status} />}
        </Toolbar>
      </AppBar>
      <Container maxWidth="md" sx={{ py: { xs: 4, md: 7 } }}>
        <Stack spacing={3}>
          {page === "make-clip" && <MakeClipScreen />}

          {page === "settings" && (
            <>
              {(settings.isLoading || health.isLoading) && (
                <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}>
                  <CircularProgress aria-label="Loading settings" />
                </Box>
              )}
              {settings.error && <Alert severity="error">{settings.error.message}</Alert>}
              {settings.data && <SettingsForm settings={settings.data} />}

              {health.error && <Alert severity="error">{health.error.message}</Alert>}
              {health.data && (
                <Card variant="outlined">
                  <CardContent>
                    <Stack direction="row" justifyContent="space-between" alignItems="center">
                      <Typography variant="h5">Runtime readiness</Typography>
                      <StatusChip status={health.data.status} />
                    </Stack>
                    <List>
                      {[
                        ["Application", health.data.application],
                        ["SQLite", health.data.database],
                        ["Jellyfin FFmpeg", health.data.media_tools],
                      ].map(([name, component]) => {
                        const item = component as typeof health.data.application;
                        return (
                          <ListItem key={name as string} disableGutters alignItems="flex-start">
                            <ListItemIcon sx={{ minWidth: 40, pt: 0.5 }}>
                              <StatusIcon status={item.status} />
                            </ListItemIcon>
                            <ListItemText primary={name as string} secondary={item.message} />
                          </ListItem>
                        );
                      })}
                    </List>
                  </CardContent>
                </Card>
              )}
            </>
          )}
        </Stack>
      </Container>
    </ThemeProvider>
  );
}
