import { Alert, FormControl, InputLabel, MenuItem, Select, Stack } from "@mui/material";

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
      {capabilities.hdr.dolby_vision &&
        !capabilities.hdr.dolby_vision_base_layer_compatible &&
        !capabilities.hdr.hdr10 &&
        !capabilities.hdr.hlg && (
        <Alert severity="warning">
          This Dolby Vision source has no compatible HDR fallback and cannot be tone-mapped.
        </Alert>
      )}
    </Stack>
  );
}

