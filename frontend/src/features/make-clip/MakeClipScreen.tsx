import ContentCutRounded from "@mui/icons-material/ContentCutRounded";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Divider,
  Stack,
  Typography,
} from "@mui/material";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  createClip,
  fetchMediaCapabilities,
} from "../../api";
import { formatTimestampMs } from "../../timestamps";
import type {
  ClipCreateRequest,
  JobSnapshot,
  PlexSession,
  PlexSessionSnapshotStatus,
} from "../../types";
import { ClipBoundaryEditor } from "./ClipBoundaryEditor";
import { displayedPosition, useClock, useJobSnapshot, useLivePlexSessions } from "./hooks";
import { JobStatus } from "./JobStatus";
import { MediaErrorAlert, structuredErrorFrom } from "./MediaErrorAlert";
import { MediaTrackSelectors } from "./MediaTrackSelectors";
import { SessionList } from "./SessionList";
import { initialTrackSelection } from "./trackSelection";

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

const ACTIVE_CLIP_JOB_KEY = "mediaclipmakarr.activeClipJobId";

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
  const [submittedJobId, setSubmittedJobId] = useState<string | null>(() =>
    typeof window === "undefined" ? null : window.sessionStorage.getItem(ACTIVE_CLIP_JOB_KEY),
  );
  const snapshot = sessions.data;
  const selectedSession = snapshot?.sessions.find(
    (session) => session.session_identity === selectedSessionIdentity,
  );
  const now = useClock(selectedSession?.state.toLowerCase() === "playing");
  const livePositionMs = selectedSession ? displayedPosition(selectedSession, now) : null;
  const selectedSessionEnded = Boolean(selectedSessionIdentity && snapshot && !selectedSession);
  const capabilitiesVersion = mediaCapabilitiesVersion(selectedSession);
  const activeJob = useJobSnapshot(submittedJob, submittedJobId);
  const capabilities = useQuery({
    queryKey: ["media-capabilities", selectedSessionIdentity, capabilitiesVersion],
    queryFn: () => fetchMediaCapabilities(selectedSessionIdentity || ""),
    enabled: Boolean(selectedSessionIdentity && selectedSession),
  });
  const clipCreate = useMutation({
    mutationFn: createClip,
    onSuccess: (job) => {
      setSubmittedJob(job);
      setSubmittedJobId(job.id);
      window.sessionStorage.setItem(ACTIVE_CLIP_JOB_KEY, job.id);
    },
  });

  const clearSubmittedJob = () => {
    setSubmittedJob(null);
    setSubmittedJobId(null);
    window.sessionStorage.removeItem(ACTIVE_CLIP_JOB_KEY);
  };

  const resetClipSubmission = () => {
    setBoundaryNotice(null);
    clearSubmittedJob();
    clipCreate.reset();
  };

  const handleStartChange = (input: string, value: number | null) => {
    setStartInput(input);
    setStartMs(value);
    resetClipSubmission();
  };

  const handleEndChange = (input: string, value: number | null) => {
    setEndInput(input);
    setEndMs(value);
    resetClipSubmission();
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
    handleStartChange("", null);
    handleEndChange("", null);
    setBoundaryNotice("The selected player changed media, so captured boundaries were cleared.");
  }, [selectedMediaIdentity, selectedSession]);

  useEffect(() => {
    if (!capabilities.data) return;
    const selection = initialTrackSelection(capabilities.data);
    setAudioStreamIndex(selection.audioStreamIndex);
    setSubtitleStreamIndex(selection.subtitleStreamIndex);
    setSubtitlesEnabled(selection.subtitlesEnabled);
  }, [capabilities.data]);

  const hasValidRange =
    startMs !== null &&
    endMs !== null &&
    endMs > startMs &&
    (selectedSession?.duration_ms == null || endMs <= selectedSession.duration_ms);

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

  const selectAlternativeTrack = (alternative: Record<string, unknown>) => {
    const streamIndex = alternative.stream_index;
    if (typeof streamIndex !== "number") return;
    if (alternative.codec_type === "audio") {
      setAudioStreamIndex(streamIndex);
    } else if (alternative.codec_type === "subtitle") {
      setSubtitleStreamIndex(streamIndex);
      setSubtitlesEnabled(true);
    }
    clearSubmittedJob();
    clipCreate.reset();
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
              const initialStartMs = displayedPosition(session, Date.now());
              setStartMs(initialStartMs);
              setStartInput(formatTimestampMs(initialStartMs));
              setEndMs(null);
              setEndInput("");
              clearSubmittedJob();
              clipCreate.reset();
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
                <ClipBoundaryEditor
                  startInput={startInput}
                  endInput={endInput}
                  startMs={startMs}
                  endMs={endMs}
                  livePositionMs={livePositionMs}
                  mediaDurationMs={selectedSession.duration_ms}
                  adjustmentSeconds={adjustmentSeconds}
                  onAdjustmentChange={setAdjustmentSeconds}
                  onStartChange={handleStartChange}
                  onEndChange={handleEndChange}
                >
                  {capabilities.isFetching && (
                    <Stack direction="row" spacing={1} alignItems="center">
                      <CircularProgress size={18} aria-label="Loading media capabilities" />
                      <Typography color="text.secondary">Loading media tracks…</Typography>
                    </Stack>
                  )}
                  {capabilities.error && (
                    <MediaErrorAlert
                      error={structuredErrorFrom(capabilities.error)}
                      fallbackMessage={capabilities.error.message}
                      onSelectAlternative={selectAlternativeTrack}
                    />
                  )}
                  <MediaTrackSelectors
                    capabilities={capabilities.data}
                    audioStreamIndex={audioStreamIndex}
                    subtitleStreamIndex={subtitleStreamIndex}
                    subtitlesEnabled={subtitlesEnabled}
                    onAudioChange={(value) => {
                      setAudioStreamIndex(value);
                      clearSubmittedJob();
                      clipCreate.reset();
                    }}
                    onSubtitleChange={(enabled, value) => {
                      setSubtitlesEnabled(enabled);
                      setSubtitleStreamIndex(value);
                      clearSubmittedJob();
                      clipCreate.reset();
                    }}
                  />
                </ClipBoundaryEditor>
                {clipCreate.error && (
                  <MediaErrorAlert
                    error={structuredErrorFrom(clipCreate.error)}
                    fallbackMessage={clipCreate.error.message}
                    onSelectAlternative={selectAlternativeTrack}
                  />
                )}
                <JobStatus job={activeJob} onSelectAlternative={selectAlternativeTrack} />

                <Button
                  startIcon={<ContentCutRounded />}
                  variant="contained"
                  disabled={!hasValidRange || clipCreate.isPending}
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

