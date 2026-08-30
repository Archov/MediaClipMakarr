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
import { type FormEvent, useEffect, useRef, useState } from "react";
import {
  createClip,
  fetchMediaCapabilities,
  fetchJob,
  fetchHealth,
  fetchPlexSessions,
  fetchSettings,
  testPlexConnection,
  updateSettings,
} from "../../api";
import { formatTimestampMs, parseTimestampMs } from "../../timestamps";
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
} from "../../types";
import { displayedPosition, useClock, useJobSnapshot, useLivePlexSessions } from "./hooks";
import { SessionDetail } from "./SessionDetail";
import { SessionList } from "./SessionList";
import { JobStatus } from "./JobStatus";
import { ClipBoundaryEditor } from "./ClipBoundaryEditor";
import { MediaTrackSelectors, selectedTrackIndex } from "./MediaTrackSelectors";

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


function jobSeverity(state: JobState): "success" | "info" | "warning" | "error" {
  if (state === "SUCCEEDED") return "success";
  if (state === "PARTIAL") return "warning";
  if (state === "FAILED") return "error";
  return "info";
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

export function MakeClipScreen() {
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

          <SessionList
            snapshot={snapshot}
            selectedSessionIdentity={selectedSessionIdentity}
            onSelect={(session) => {
              if (session.session_identity === selectedSessionIdentity) return;
              setSelectedSessionIdentity(session.session_identity);
              setSelectedMediaIdentity(session.media_identity);
              setBoundary("start", displayedPosition(session, Date.now()));
              setBoundary("end", null);
              setBoundaryNotice(null);
              setAudioStreamIndex("");
              setSubtitleStreamIndex("");
              setSubtitlesEnabled(false);
            }}
          />

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

                <ClipBoundaryEditor
                  adjustmentSeconds={adjustmentSeconds}
                  startMs={startMs}
                  endMs={endMs}
                  onAdjustmentChange={setAdjustmentSeconds}
                  onAdjustStart={adjustStart}
                  onAdjustEnd={adjustEnd}
                />

                {startMs !== null && endMs !== null && endMs > startMs && (
                  <Typography color="text.secondary" sx={{ fontVariantNumeric: "tabular-nums" }}>
                    Selected duration {formatMilliseconds(endMs - startMs)}
                  </Typography>
                )}
                {rangeError && <Alert severity="warning">{rangeError}</Alert>}
                {clipCreate.error && <Alert severity="error">{clipCreate.error.message}</Alert>}
                <JobStatus job={activeJob} />

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

