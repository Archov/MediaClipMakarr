import ArrowDownwardRounded from "@mui/icons-material/ArrowDownwardRounded";
import DeleteOutlineRounded from "@mui/icons-material/DeleteOutlineRounded";
import EditRounded from "@mui/icons-material/EditRounded";
import { Alert, Box, Button, Chip, LinearProgress, Stack } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  deleteClip,
  fetchClip,
  fetchJob,
  updateClipMetadata,
} from "../../api";
import { formatTimestampMs } from "../../timestamps";
import type { ClipMetadataUpdate, ClipRecord, JobSnapshot, JobState } from "../../types";
import { DeleteClipDialog, MetadataDialog } from "../library/ClipDialogs";
import { MediaErrorAlert } from "./MediaErrorAlert";

function severity(state: JobState): "success" | "info" | "warning" | "error" {
  if (state === "SUCCEEDED") return "success";
  if (state === "PARTIAL") return "warning";
  if (state === "FAILED") return "error";
  return "info";
}

export function JobStatus({
  job,
  onSelectAlternative,
}: {
  job: JobSnapshot | null;
  onSelectAlternative?: (alternative: Record<string, unknown>) => void;
}) {
  const queryClient = useQueryClient();
  const clipId = job?.state === "SUCCEEDED" && job.result ? job.result.clip_id : null;
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const [deleteWarnings, setDeleteWarnings] = useState<string[]>([]);
  const [editJobId, setEditJobId] = useState<string | null>(null);
  const clip = useQuery({
    queryKey: ["clip", clipId],
    queryFn: () => fetchClip(clipId!),
    enabled: Boolean(clipId && !deleted),
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

  if (!job) return null;
  const editJobBusy = Boolean(
    editJobId && !["SUCCEEDED", "FAILED", "PARTIAL"].includes(editJob.data?.state ?? ""),
  );
  return <Stack spacing={2}>
    {!job.error && <Alert severity={severity(job.state)}>{job.message}{job.queue_position ? ` Queue position ${job.queue_position}.` : ""}</Alert>}
    {job.error && (
      <MediaErrorAlert error={job.error} onSelectAlternative={onSelectAlternative} />
    )}
    {job.state !== "SUCCEEDED" && job.state !== "FAILED" && <LinearProgress variant="determinate" value={Math.round(job.progress * 100)} aria-label="Clip render progress" />}
    {job.state === "SUCCEEDED" && job.result && !deleted && <Stack spacing={2}>
      <Box component="video" src={job.result.play_url} poster={clip.data?.thumbnail_url} controls sx={{ width: "100%", borderRadius: 1, bgcolor: "black" }} />
      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
        <Chip label={clip.data?.title ?? job.result.title} color="success" variant="outlined" />
        <Chip label={formatTimestampMs(job.result.duration_ms) || "--:--"} variant="outlined" />
        <Button href={job.result.download_url} variant="outlined" startIcon={<ArrowDownwardRounded />}>Download</Button>
        <Button
          variant="outlined"
          startIcon={<EditRounded />}
          disabled={!clip.data}
          onClick={() => setEditing(true)}
        >
          Edit
        </Button>
        <Button
          color="error"
          variant="outlined"
          startIcon={<DeleteOutlineRounded />}
          disabled={!clip.data}
          onClick={() => {
            deleteMutation.reset();
            setDeleting(true);
          }}
        >
          Delete
        </Button>
      </Stack>
    </Stack>}
    {clip.error && <Alert severity="error">{clip.error.message}</Alert>}
    {deleted && <Alert severity="success">The generated clip was deleted.</Alert>}
    {deleteWarnings.length > 0 && <Alert severity="warning">{deleteWarnings.join(" ")}</Alert>}
    {editing && clip.data && (
      <MetadataDialog
        clip={clip.data}
        busy={editMutation.isPending || editJobBusy}
        error={editMutation.error?.message ?? editJob.data?.error?.message ?? null}
        onClose={() => {
          if (!editJobBusy) setEditing(false);
        }}
        onSave={(update) => editMutation.mutate({ clip: clip.data, update })}
      />
    )}
    {deleting && clip.data && (
      <DeleteClipDialog
        clip={clip.data}
        busy={deleteMutation.isPending}
        error={deleteMutation.error?.message ?? null}
        onClose={() => {
          deleteMutation.reset();
          setDeleting(false);
        }}
        onConfirm={() => deleteMutation.mutate(clip.data)}
      />
    )}
  </Stack>;
}

