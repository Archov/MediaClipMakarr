import ArrowDownwardRounded from "@mui/icons-material/ArrowDownwardRounded";
import DeleteOutlineRounded from "@mui/icons-material/DeleteOutlineRounded";
import EditRounded from "@mui/icons-material/EditRounded";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  LinearProgress,
  Stack,
  Tooltip,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  deleteClip,
  fetchClip,
  fetchJob,
  fetchSettings,
  updateClipMetadata,
  uploadClipToImmich,
} from "../../api";
import { formatTimestampMs } from "../../timestamps";
import type {
  ClipMetadataUpdate,
  ClipRecord,
  JobSnapshot,
  JobState,
} from "../../types";
import { DeleteClipDialog, MetadataDialog } from "../library/ClipDialogs";
import { ImmichIcon } from "../library/LibraryScreen";
import { MediaErrorAlert } from "./MediaErrorAlert";

const NONTERMINAL_JOB_STATES = new Set(["QUEUED", "RUNNING", "FINALIZING"]);

function severity(state: JobState): "success" | "info" | "warning" | "error" {
  if (state === "SUCCEEDED") return "success";
  if (state === "PARTIAL") return "warning";
  if (state === "FAILED") return "error";
  return "info";
}

export function JobStatus({
  job,
  initialClip = null,
  isLoading = false,
  loadError = null,
  onSelectAlternative,
  onDismiss,
}: {
  job: JobSnapshot | null;
  initialClip?: ClipRecord | null;
  isLoading?: boolean;
  loadError?: string | null;
  onSelectAlternative?: (alternative: Record<string, unknown>) => void;
  onDismiss?: () => void;
}) {
  const queryClient = useQueryClient();
  const jobClipId = job?.state === "SUCCEEDED" && job.result && "clip_id" in job.result ? job.result.clip_id : null;
  const clipId = jobClipId ?? initialClip?.id ?? null;
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const [deleteWarnings, setDeleteWarnings] = useState<string[]>([]);
  const [editJobId, setEditJobId] = useState<string | null>(null);
  const clip = useQuery({
    queryKey: ["clip", jobClipId],
    queryFn: () => fetchClip(jobClipId!),
    enabled: Boolean(jobClipId && !deleted),
    // Auto-upload (or a prior manual upload) runs as its own job, independent
    // of the clip-create job this screen otherwise tracks — poll while it's
    // still in flight so its outcome shows up without a manual refresh.
    refetchInterval: (query) => {
      const state = query.state.data?.immich_upload_job?.state;
      return state && NONTERMINAL_JOB_STATES.has(state) ? 1_000 : false;
    },
  });
  const editJob = useQuery({
    queryKey: ["job", editJobId],
    queryFn: () => fetchJob(editJobId!),
    enabled: Boolean(editJobId),
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state && ["SUCCEEDED", "FAILED", "PARTIAL"].includes(state) ? false : 1_000;
    },
  });
  const editMutation = useMutation({
    mutationFn: ({ clip: target, update }: { clip: ClipRecord; update: ClipMetadataUpdate }) =>
      updateClipMetadata(target.id, update),
    onSuccess: (submitted) => setEditJobId(submitted.id),
  });
  const deleteMutation = useMutation({
    mutationFn: (target: ClipRecord) => deleteClip(target.id, target.revision),
    onSuccess: (result) => {
      setDeleting(false);
      setDeleted(true);
      setDeleteWarnings(result.cleanup_warnings);
      void queryClient.invalidateQueries({ queryKey: ["clips"] });
      void queryClient.invalidateQueries({ queryKey: ["clip-libraries"] });
      void queryClient.removeQueries({ queryKey: ["clip", result.id] });
    },
  });
  const settings = useQuery({ queryKey: ["settings"], queryFn: fetchSettings });
  const immichConfigured = Boolean(
    settings.data?.immich_url && settings.data?.immich_api_key_configured,
  );
  const uploadMutation = useMutation({
    mutationFn: (target: ClipRecord) => uploadClipToImmich(target.id),
    onSuccess: () => {
      // Auto-upload (and any prior manual upload) runs as its own independent
      // job — refetch rather than trust a stale snapshot, since `displayedClip`
      // may be backed by either the `["clip", jobClipId]` query or the parent's
      // "newest clip" list query depending on whether a render job is active.
      void queryClient.invalidateQueries({ queryKey: ["clip", clipId] });
      void queryClient.invalidateQueries({ queryKey: ["clips", "make-clip-latest"] });
    },
  });

  useEffect(() => {
    setEditing(false);
    setDeleting(false);
    setDeleted(false);
    setDeleteWarnings([]);
    setEditJobId(null);
    editMutation.reset();
    deleteMutation.reset();
  }, [clipId]);

  useEffect(() => {
    if (editJob.data?.state !== "SUCCEEDED" || !clipId) return;
    void queryClient.invalidateQueries({ queryKey: ["clip", clipId] });
    void queryClient.invalidateQueries({ queryKey: ["clips"] });
    void queryClient.invalidateQueries({ queryKey: ["clip-libraries"] });
    setEditing(false);
    setEditJobId(null);
  }, [clipId, editJob.data?.state, queryClient]);

  const displayedClip = clip.data ?? initialClip;
  const completedResult =
    job?.state === "SUCCEEDED" && job.result && "play_url" in job.result
      ? job.result
      : null;
  const showClip = Boolean(!deleted && (completedResult || (!job && displayedClip)));
  const playUrl = completedResult?.play_url ?? displayedClip?.play_url;
  const downloadUrl = completedResult?.download_url ?? displayedClip?.download_url;
  const title = displayedClip?.title ?? completedResult?.title ?? "Clip";
  const durationMs = displayedClip?.duration_ms ?? completedResult?.duration_ms ?? null;
  const editJobBusy = Boolean(
    editJobId && !["SUCCEEDED", "FAILED", "PARTIAL"].includes(editJob.data?.state ?? ""),
  );
  const uploadJob = displayedClip?.immich_upload_job;
  const uploading = Boolean(uploadJob && NONTERMINAL_JOB_STATES.has(uploadJob.state));
  const uploadLabel = uploading
    ? "Uploading to Immich…"
    : uploadJob?.state === "PARTIAL" || uploadJob?.state === "FAILED"
      ? "Retry Immich upload"
      : displayedClip?.immich_asset_id
        ? "Re-upload to Immich"
        : "Upload to Immich";
  const uploadDetail =
    uploadJob?.state === "PARTIAL" || uploadJob?.state === "FAILED"
      ? (uploadJob.error ? `${uploadJob.error.code}: ${uploadJob.error.message}` : uploadLabel)
      : null;
  return <Stack spacing={2}>
    {job && !job.error && <Alert severity={severity(job.state)}>{job.message}{job.queue_position ? ` Queue position ${job.queue_position}.` : ""}</Alert>}
    {job?.error && (
      <MediaErrorAlert error={job.error} onSelectAlternative={onSelectAlternative} />
    )}
    {job && (job.state === "FAILED" || job.state === "PARTIAL") && onDismiss && (
      <Button variant="text" onClick={onDismiss} sx={{ alignSelf: "flex-start" }}>
        Dismiss
      </Button>
    )}
    {job && job.state !== "SUCCEEDED" && job.state !== "FAILED" && <LinearProgress variant="determinate" value={Math.round(job.progress * 100)} aria-label="Clip render progress" />}
    {!job && isLoading && <Stack direction="row" spacing={1} alignItems="center"><CircularProgress size={18} aria-label="Loading newest clip" /><span>Loading newest clip…</span></Stack>}
    {!job && loadError && <Alert severity="error">{loadError}</Alert>}
    {!job && !isLoading && !loadError && !displayedClip && <Alert severity="info">No clips made yet.</Alert>}
    {showClip && <Stack spacing={2}>
      <Box component="video" src={playUrl} poster={displayedClip?.thumbnail_url} controls sx={{ width: "100%", borderRadius: 1, bgcolor: "black" }} />
      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
        <Chip label={title} color="success" variant="outlined" />
        <Chip label={formatTimestampMs(durationMs) || "--:--"} variant="outlined" />
      </Stack>
      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
        <Button href={downloadUrl} variant="outlined" startIcon={<ArrowDownwardRounded />}>Download</Button>
        <Button
          variant="outlined"
          startIcon={<EditRounded />}
          disabled={!displayedClip}
          onClick={() => setEditing(true)}
        >
          Edit
        </Button>
        {immichConfigured && (
          <Tooltip title={uploadDetail ?? ""} disableHoverListener={!uploadDetail}>
            <span>
              <Button
                variant="outlined"
                startIcon={<ImmichIcon uploaded={Boolean(displayedClip?.immich_asset_id)} />}
                disabled={!displayedClip || uploading}
                onClick={() => displayedClip && uploadMutation.mutate(displayedClip)}
              >
                {uploadLabel}
              </Button>
            </span>
          </Tooltip>
        )}
        <Button
          color="error"
          variant="outlined"
          startIcon={<DeleteOutlineRounded />}
          disabled={!displayedClip}
          onClick={() => {
            deleteMutation.reset();
            setDeleting(true);
          }}
        >
          Delete
        </Button>
      </Stack>
    </Stack>}
    {uploadMutation.error && (
      <Alert severity="error" onClose={() => uploadMutation.reset()}>
        {uploadMutation.error.message}
      </Alert>
    )}
    {clip.error && <Alert severity="error">{clip.error.message}</Alert>}
    {deleted && <Alert severity="success">The generated clip was deleted.</Alert>}
    {deleteWarnings.length > 0 && <Alert severity="warning">{deleteWarnings.join(" ")}</Alert>}
    {editing && displayedClip && (
      <MetadataDialog
        clip={displayedClip}
        busy={editMutation.isPending || editJobBusy}
        error={editMutation.error?.message ?? editJob.data?.error?.message ?? null}
        onClose={() => {
          if (!editJobBusy) setEditing(false);
        }}
        onSave={(update) => editMutation.mutate({ clip: displayedClip, update })}
      />
    )}
    {deleting && displayedClip && (
      <DeleteClipDialog
        clip={displayedClip}
        busy={deleteMutation.isPending}
        error={deleteMutation.error?.message ?? null}
        onClose={() => {
          deleteMutation.reset();
          setDeleting(false);
        }}
        onConfirm={() => deleteMutation.mutate(displayedClip)}
      />
    )}
  </Stack>;
}

