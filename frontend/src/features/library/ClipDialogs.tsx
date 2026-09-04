import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import { useEffect, useState, type FormEvent } from "react";

import type { ClipMetadataUpdate, ClipRecord } from "../../types";

export function DeleteClipDialog({
  clip,
  busy,
  error,
  showImmichToggle = false,
  onClose,
  onConfirm,
}: {
  clip: ClipRecord;
  busy: boolean;
  error: string | null;
  showImmichToggle?: boolean;
  onClose: () => void;
  onConfirm: (deleteFromImmich: boolean) => void;
}) {
  const [ready, setReady] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [deleteFromImmich, setDeleteFromImmich] = useState(false);
  useEffect(() => {
    const timeout = window.setTimeout(() => setReady(true), 1_000);
    return () => window.clearTimeout(timeout);
  }, []);
  useEffect(() => {
    if (error) setSubmitted(false);
  }, [error]);
  const confirm = () => {
    if (!ready || busy || submitted) return;
    setSubmitted(true);
    onConfirm(deleteFromImmich);
  };
  const confirmLabel = !ready
    ? "Confirm in 1 second…"
    : busy || submitted
      ? "Deleting…"
      : deleteFromImmich
        ? "Delete from MCM & Immich"
        : "Delete from MCM";
  return (
    <Dialog open onClose={busy ? undefined : onClose} fullWidth maxWidth="sm">
      <DialogTitle>Delete “{clip.title}”?</DialogTitle>
      <DialogContent>
        <Stack spacing={2}>
          <Alert severity="warning">
            This permanently removes the generated clip and its thumbnail. The original source
            media is never deleted.
          </Alert>
          {error && <Alert severity="error">{error}</Alert>}
          <Box
            component="video"
            src={clip.play_url}
            poster={`${clip.thumbnail_url}?revision=${clip.revision}`}
            controls
            preload="metadata"
            sx={{ width: "100%", maxHeight: 360, bgcolor: "black" }}
          />
          {showImmichToggle && (
            <Stack
              direction="row"
              spacing={2}
              alignItems="center"
              justifyContent="space-between"
              sx={{ p: 1.5, borderRadius: 1, border: 1, borderColor: "divider" }}
            >
              <Stack spacing={0}>
                <Typography variant="body2" fontWeight={600}>Also delete from Immich</Typography>
                <Typography variant="caption" color="text.secondary">
                  {deleteFromImmich
                    ? "The Immich copy will be deleted too."
                    : "Only the local clip is deleted."}
                </Typography>
              </Stack>
              <Switch
                checked={deleteFromImmich}
                disabled={busy}
                onChange={(event) => setDeleteFromImmich(event.target.checked)}
              />
            </Stack>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Nevermind</Button>
        <Button
          color="error"
          variant="contained"
          disabled={!ready || busy || submitted}
          onClick={confirm}
        >
          {confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export function ImmichPermissionDialog({
  settingsUrl,
  onClose,
}: {
  settingsUrl: string;
  onClose: () => void;
}) {
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>Missing Immich permission</DialogTitle>
      <DialogContent>
        <Alert severity="warning">
          The configured API key does not have the <code>asset.read</code> permission. Please
          update the permissions and try again.
        </Alert>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Dismiss</Button>
        <Button
          variant="contained"
          component="a"
          href={settingsUrl}
          target="_blank"
          rel="noopener noreferrer"
          onClick={onClose}
        >
          Open API Key Settings
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export function ImmichAssetMissingDialog({
  busy,
  onClose,
  onReupload,
}: {
  busy: boolean;
  onClose: () => void;
  onReupload: () => void;
}) {
  return (
    <Dialog open onClose={busy ? undefined : onClose} fullWidth maxWidth="xs">
      <DialogTitle>Immich asset missing</DialogTitle>
      <DialogContent>
        <Alert severity="warning">The associated Immich asset couldn’t be found.</Alert>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Dismiss</Button>
        <Button variant="contained" disabled={busy} onClick={onReupload}>
          {busy ? "Reuploading…" : "Reupload"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export function ImmichDeletePermissionDialog({
  settingsUrl,
  busy,
  error,
  onClose,
  onRetry,
}: {
  settingsUrl: string;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onRetry: () => void;
}) {
  return (
    <Dialog open onClose={busy ? undefined : onClose} fullWidth maxWidth="xs">
      <DialogTitle>Missing Immich permission</DialogTitle>
      <DialogContent>
        <Stack spacing={2}>
          <Alert severity="warning">
            The clip was deleted, but the configured API key does not have the{" "}
            <code>asset.delete</code> permission, so the Immich asset could not be removed.
            Update the permissions, then retry.
          </Alert>
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Dismiss</Button>
        <Button
          component="a"
          href={settingsUrl}
          target="_blank"
          rel="noopener noreferrer"
          disabled={busy}
        >
          Open API Key Settings
        </Button>
        <Button variant="contained" disabled={busy} onClick={onRetry}>
          {busy ? "Retrying…" : "Retry Delete"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export function MetadataDialog({
  clip,
  busy,
  error,
  onClose,
  onSave,
}: {
  clip: ClipRecord;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSave: (update: ClipMetadataUpdate) => void;
}) {
  const [form, setForm] = useState({
    custom_title: clip.custom_title ?? "",
    library: clip.library,
    media_type: clip.media_type,
    movie_title: clip.movie_title ?? "",
    movie_year: clip.movie_year?.toString() ?? "",
    show_name: clip.show_name ?? "",
    episode_title: clip.episode_title ?? "",
    season_number: clip.season_number?.toString() ?? "",
    episode_number: clip.episode_number?.toString() ?? "",
  });
  const field = (name: keyof typeof form) => ({
    value: form[name],
    onChange: (event: { target: { value: string } }) =>
      setForm((current) => ({ ...current, [name]: event.target.value })),
  });
  const optionalNumber = (value: string) => value ? Number(value) : null;
  const canSave = !busy && Boolean(form.library.trim());
  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!canSave) return;
    onSave({
      expected_revision: clip.revision,
      custom_title: form.custom_title || null,
      library: form.library,
      media_type: form.media_type as "movie" | "episode" | "video",
      movie_title: form.movie_title || null,
      movie_year: optionalNumber(form.movie_year),
      show_name: form.show_name || null,
      episode_title: form.episode_title || null,
      season_number: optionalNumber(form.season_number),
      episode_number: optionalNumber(form.episode_number),
    });
  };
  return (
    <Dialog open onClose={busy ? undefined : onClose} fullWidth maxWidth="sm">
      <DialogTitle>Edit clip details</DialogTitle>
      <Box component="form" onSubmit={handleSubmit}>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {error && <Alert severity="error">{error}</Alert>}
            <TextField
              label="Custom clip title"
              helperText="Clear to restore the automatic title."
              {...field("custom_title")}
            />
            <TextField label="Library" required {...field("library")} />
            <FormControl>
              <InputLabel>Media type</InputLabel>
              <Select label="Media type" {...field("media_type")}>
                <MenuItem value="movie">Movie</MenuItem>
                <MenuItem value="episode">Episode</MenuItem>
                <MenuItem value="video">Video</MenuItem>
              </Select>
            </FormControl>
            {form.media_type === "movie" && (
              <>
                <TextField label="Movie title" {...field("movie_title")} />
                <TextField label="Year" type="number" {...field("movie_year")} />
              </>
            )}
            {form.media_type === "episode" && (
              <>
                <TextField label="Show" {...field("show_name")} />
                <TextField label="Episode title" {...field("episode_title")} />
                <Stack direction="row" spacing={2}>
                  <TextField label="Season" type="number" {...field("season_number")} />
                  <TextField label="Episode" type="number" {...field("episode_number")} />
                </Stack>
              </>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button type="button" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="submit" variant="contained" disabled={!canSave}>
            {busy ? "Updating…" : "Save"}
          </Button>
        </DialogActions>
      </Box>
    </Dialog>
  );
}
