import ContentCutRounded from "@mui/icons-material/ContentCutRounded";
import {
  Alert,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  createClip,
  fetchClips,
  fetchMediaCapabilities,
} from "../../api";
import { formatTimestampMs } from "../../timestamps";
import type {
  ClipCreateRequest,
  JobSnapshot,
  PlexSession,
} from "../../types";
import { ClipBoundaryEditor } from "./ClipBoundaryEditor";
import { displayedPosition, useClock, useJobSnapshot, useLivePlexSessions } from "./hooks";
import { JobStatus } from "./JobStatus";
import { MediaErrorAlert, structuredErrorFrom } from "./MediaErrorAlert";
import { MediaTrackSelectors } from "./MediaTrackSelectors";
import { SessionList } from "./SessionList";
import { initialTrackSelection } from "./trackSelection";

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
  const queryClient = useQueryClient();
  const latestClip = useQuery({
    queryKey: ["clips", "make-clip-latest"],
    queryFn: () => fetchClips({ page: 1, pageSize: 1, sort: "newest" }),
  });
  const newestClip = latestClip.data?.items[0] ?? null;
  const activeJobIsRunning = Boolean(
    activeJob && !["SUCCEEDED", "PARTIAL", "FAILED"].includes(activeJob.state),
  );
  const activeJobMatchesNewest = Boolean(
    activeJob?.state === "SUCCEEDED" &&
      activeJob.result &&
      "clip_id" in activeJob.result &&
      activeJob.result.clip_id === newestClip?.id,
  );
  // A reconnected job that finished FAILED/PARTIAL (e.g. after a page reload
  // while it was still running) has no way to "match newest" — there's no
  // successful clip to compare against — but the user still needs to see the
  // error and any recovery actions, not an empty state or an older clip.
  const activeJobNeedsAttention = Boolean(
    activeJob && (activeJob.state === "FAILED" || activeJob.state === "PARTIAL"),
  );
  const displayedJob =
    submittedJob || activeJobIsRunning || activeJobMatchesNewest || activeJobNeedsAttention
      ? activeJob
      : null;

  useEffect(() => {
    // The "newest clip" query only runs once on mount, so a clip finished by a
    // reconnected job (job snapshot arriving via SSE, not this mutation's own
    // onSuccess) would otherwise never be reflected here — refetch it as soon
    // as the active job reaches SUCCEEDED so activeJobMatchesNewest can catch up.
    if (activeJob?.state === "SUCCEEDED") {
      void queryClient.invalidateQueries({ queryKey: ["clips", "make-clip-latest"] });
    }
  }, [activeJob?.state, queryClient]);
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

  const resetClipSubmission = () => {
    setBoundaryNotice(null);
    clipCreate.reset();
  };

  const dismissActiveJob = () => {
    setSubmittedJob(null);
    setSubmittedJobId(null);
    window.sessionStorage.removeItem(ACTIVE_CLIP_JOB_KEY);
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
    clipCreate.reset();
  };

  return (
    <Stack spacing={3} sx={{ maxWidth: 760, mx: "auto" }}>
      <Card variant="outlined">
        <CardContent>
          <Stack spacing={2}>
            {sessions.isFetching && (
              <Stack direction="row" justifyContent="flex-end">
                <CircularProgress size={20} aria-label="Refreshing sessions" />
              </Stack>
            )}
            {sessions.error && <Alert severity="error">{sessions.error.message}</Alert>}
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
                clipCreate.reset();
                setBoundaryNotice(null);
                setAudioStreamIndex("");
                setSubtitleStreamIndex("");
                setSubtitlesEnabled(false);
              }}
            />
          </Stack>
        </CardContent>
      </Card>

      <Card variant="outlined">
        <CardContent>
          {!selectedSession ? (
            <Alert severity="info">Select a Plex session to create a clip.</Alert>
          ) : (
            <Stack spacing={2}>
              <ClipBoundaryEditor
                startInput={startInput}
                endInput={endInput}
                startMs={startMs}
                endMs={endMs}
                livePositionMs={livePositionMs}
                sessionIdentity={selectedSession.session_identity}
                mediaIdentity={selectedSession.media_identity}
                mediaDurationMs={selectedSession.duration_ms}
                mediaFrameRate={capabilities.data?.frame_rate ?? null}
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
                    clipCreate.reset();
                  }}
                  onSubtitleChange={(enabled, value) => {
                    setSubtitlesEnabled(enabled);
                    setSubtitleStreamIndex(value);
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
          )}
        </CardContent>
      </Card>

      <Card variant="outlined">
        <CardContent>
          <JobStatus
            job={displayedJob}
            initialClip={displayedJob ? null : newestClip}
            isLoading={!displayedJob && latestClip.isLoading}
            loadError={!displayedJob ? latestClip.error?.message ?? null : null}
            onSelectAlternative={selectAlternativeTrack}
            onDismiss={activeJobNeedsAttention ? dismissActiveJob : undefined}
          />
        </CardContent>
      </Card>
    </Stack>
  );
}

