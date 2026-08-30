import { Alert, FormControl, InputLabel, MenuItem, Select, Stack, Typography } from "@mui/material";

import type { MediaCapabilities, TrackDescriptor } from "../../types";

function trackLabel(track: TrackDescriptor): string {
  const parts = [
    track.language?.toUpperCase(),
    track.title,
    track.codec,
    track.stream_index === null ? null : `#${track.stream_index}`,
  ].filter(Boolean);
  return parts.join(" · ") || "Unnamed track";
}

export function selectedTrackIndex(tracks: TrackDescriptor[], fallback: number | null): number | "" {
  const available = tracks.filter((track) => track.available && track.stream_index !== null);
  const selected = available.find((track) => track.selected);
  const fallbackTrack = available.find((track) => track.stream_index === fallback);
  return selected?.stream_index ?? fallbackTrack?.stream_index ?? available[0]?.stream_index ?? "";
}

export function MediaTrackSelectors({
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
      {capabilities.warnings.map((warning) => (
        <Alert key={warning} severity="warning">{warning}</Alert>
      ))}
      {capabilities.hdr.dolby_vision && (
        <Alert severity="warning">Dolby Vision rendering is unavailable.</Alert>
      )}
      {(capabilities.hdr.hdr10 || capabilities.hdr.hlg) && (
        <Alert severity="info">HDR source detected. SDR tone mapping is handled in a later phase.</Alert>
      )}
    </Stack>
  );
}

