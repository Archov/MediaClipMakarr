import ArrowDownwardRounded from "@mui/icons-material/ArrowDownwardRounded";
import DeleteOutlineRounded from "@mui/icons-material/DeleteOutlineRounded";
import EditRounded from "@mui/icons-material/EditRounded";
import GifRounded from "@mui/icons-material/GifRounded";
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
  checkImmichAsset,
  deleteClip,
  fetchClip,
  fetchJob,
  fetchSettings,
  retryImmichAssetDelete,
  reuploadClipToImmich,
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
import { useGifExport } from "../gif-export/useGifExport";
import {
  DeleteClipDialog,
  ImmichAssetMissingDialog,
  ImmichDeletePermissionDialog,
  ImmichPermissionDialog,
  MetadataDialog,
} from "../library/ClipDialogs";
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
    mutationFn: ({ target, deleteFromImmich }: { target: ClipRecord; deleteFromImmich: boolean }) =>
      deleteClip(target.id, target.revision, deleteFromImmich),
    onSuccess: (result) => {
      setDeleting(false);
      setDeleted(true);
      setDeleteWarnings(result.cleanup_warnings);
      void queryClient.invalidateQueries({ queryKey: ["clips"] });
      void queryClient.invalidateQueries({ queryKey: ["clip-libraries"] });
      void queryClient.removeQueries({ queryKey: ["clip", result.id] });
      if (result.immich_delete_missing_permission) {
        setImmichDeleteIssue({
          retryToken: result.immich_delete_missing_permission.retry_token,
          settingsUrl: result.immich_delete_missing_permission.settings_url,
        });
      }
    },
  });
  const [immichDeleteIssue, setImmichDeleteIssue] = useState<
    { retryToken: string; settingsUrl: string } | null
  >(null);
  const retryImmichDeleteMutation = useMutation({
    mutationFn: (retryToken: string) => retryImmichAssetDelete(retryToken),
    onSuccess: (result, retryToken) => {
      if (result.status === "ok") {
        setImmichDeleteIssue(null);
        return;
      }
      setImmichDeleteIssue({ retryToken, settingsUrl: result.settings_url ?? "" });
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
  const [immichIssue, setImmichIssue] = useState<
    { clip: ClipRecord; status: "missing_permission" | "asset_missing" } | null
  >(null);
  const [immichSettingsUrl, setImmichSettingsUrl] = useState<string | null>(null);
  const checkImmichMutation = useMutation({
    mutationFn: (target: ClipRecord) => checkImmichAsset(target.id),
    onSuccess: (result, target) => {
      if (result.status === "ok") {
        if (result.open_url) window.open(result.open_url, "_blank", "noopener,noreferrer");
        return;
      }
      if (result.status === "asset_missing") {
        // The backend already cleared the stale association the moment it
        // confirmed the asset was gone — refetch immediately so the icon
        // reverts without waiting on the user to dismiss the dialog.
        void queryClient.invalidateQueries({ queryKey: ["clip", clipId] });
        void queryClient.invalidateQueries({ queryKey: ["clips", "make-clip-latest"] });
      }
      // Bind to the clip that was actually checked (`target`), not whatever
      // `displayedClip` re-derives to later — this screen can stay mounted
      // across a `clipId` change (e.g. starting a new clip while this
      // dialog is open), and a reupload must never fire against a
      // different clip than the one confirmed missing.
      setImmichIssue({ clip: target, status: result.status });
      setImmichSettingsUrl(result.settings_url);
    },
  });
  const reuploadMutation = useMutation({
    mutationFn: (target: ClipRecord) => reuploadClipToImmich(target.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["clip", clipId] });
      void queryClient.invalidateQueries({ queryKey: ["clips", "make-clip-latest"] });
      setImmichIssue(null);
    },
  });
  const gifExport = useGifExport(clipId);

  useEffect(() => {
    setEditing(false);
    setDeleting(false);
    setDeleted(false);
    setDeleteWarnings([]);
    setEditJobId(null);
    editMutation.reset();
    deleteMutation.reset();
    setImmichIssue(null);
    setImmichSettingsUrl(null);
    setImmichDeleteIssue(null);
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
  const needsRetry = uploadJob?.state === "PARTIAL" || uploadJob?.state === "FAILED";
  const isLinked = Boolean(displayedClip?.immich_asset_id);
  const uploadLabel = uploading
    ? "Uploading to Immich…"
    : needsRetry
      ? "Retry Immich upload"
      : isLinked
        ? "Open in Immich"
        : "Upload to Immich";
  const uploadDetail = needsRetry
    ? (uploadJob?.error ? `${uploadJob.error.code}: ${uploadJob.error.message}` : uploadLabel)
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
          startIcon={<GifRounded />}
          disabled={!clipId || gifExport.busy}
          onClick={() => gifExport.exportGif()}
        >
          {gifExport.busy ? "Exporting GIF…" : "Export GIF"}
        </Button>
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
                startIcon={<ImmichIcon uploaded={isLinked} />}
                disabled={!displayedClip || uploading}
                onClick={() => {
                  if (!displayedClip) return;
                  if (isLinked && !needsRetry) checkImmichMutation.mutate(displayedClip);
                  else uploadMutation.mutate(displayedClip);
                }}
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
    {checkImmichMutation.error && (
      <Alert severity="error" onClose={() => checkImmichMutation.reset()}>
        {checkImmichMutation.error.message}
      </Alert>
    )}
    {reuploadMutation.error && (
      <Alert severity="error" onClose={() => reuploadMutation.reset()}>
        {reuploadMutation.error.message}
      </Alert>
    )}
    {gifExport.error && <Alert severity="error">{gifExport.error}</Alert>}
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
        showImmichToggle={
          Boolean(settings.data?.immich_manage_remote) && Boolean(displayedClip.immich_asset_id)
        }
        onClose={() => {
          deleteMutation.reset();
          setDeleting(false);
        }}
        onConfirm={(deleteFromImmich) =>
          deleteMutation.mutate({ target: displayedClip, deleteFromImmich })
        }
      />
    )}
    {immichIssue?.status === "missing_permission" && immichSettingsUrl && (
      <ImmichPermissionDialog
        settingsUrl={immichSettingsUrl}
        onClose={() => setImmichIssue(null)}
      />
    )}
    {immichIssue?.status === "asset_missing" && (
      <ImmichAssetMissingDialog
        busy={reuploadMutation.isPending}
        onClose={() => setImmichIssue(null)}
        onReupload={() => reuploadMutation.mutate(immichIssue.clip)}
      />
    )}
    {immichDeleteIssue && (
      <ImmichDeletePermissionDialog
        settingsUrl={immichDeleteIssue.settingsUrl}
        busy={retryImmichDeleteMutation.isPending}
        error={retryImmichDeleteMutation.error?.message ?? null}
        onClose={() => {
          retryImmichDeleteMutation.reset();
          setImmichDeleteIssue(null);
        }}
        onRetry={() => retryImmichDeleteMutation.mutate(immichDeleteIssue.retryToken)}
      />
    )}
  </Stack>;
}

