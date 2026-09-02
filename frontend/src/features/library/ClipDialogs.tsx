import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  TextField,
} from "@mui/material";
import { useEffect, useState } from "react";

import type { ClipMetadataUpdate, ClipRecord } from "../../types";

export function DeleteClipDialog({
  clip,
  busy,
  error,
  onClose,
  onConfirm,
}: {
  clip: ClipRecord;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const [ready, setReady] = useState(false);
  const [submitted, setSubmitted] = useState(false);
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
    onConfirm();
  };
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
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button
          color="error"
          variant="contained"
          disabled={!ready || busy || submitted}
          onClick={confirm}
        >
          {!ready
            ? "Confirm in 1 second…"
            : busy || submitted
              ? "Deleting…"
              : "Confirm delete"}
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
  return (
    <Dialog open onClose={busy ? undefined : onClose} fullWidth maxWidth="sm">
      <DialogTitle>Edit clip details</DialogTitle>
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
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button
          variant="contained"
          disabled={busy || !form.library.trim()}
          onClick={() => onSave({
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
          })}
        >
          {busy ? "Updating…" : "Save"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
