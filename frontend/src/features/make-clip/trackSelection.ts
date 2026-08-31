import type { MediaCapabilities, TrackDescriptor } from "../../types";

type TrackSelectionCapabilities = Pick<
  MediaCapabilities,
  | "audio_tracks"
  | "subtitle_tracks"
  | "default_audio_stream_index"
  | "default_subtitle_stream_index"
  | "subtitles_forced_off"
>;

function selectedTrackIndex(
  tracks: TrackDescriptor[],
  fallback: number | null,
): number | "" {
  const available = tracks.filter((track) => track.available && track.stream_index !== null);
  const selected = available.find((track) => track.selected);
  const fallbackTrack = available.find((track) => track.stream_index === fallback);
  return selected?.stream_index ?? fallbackTrack?.stream_index ?? available[0]?.stream_index ?? "";
}

export function initialTrackSelection(capabilities: TrackSelectionCapabilities) {
  const audioStreamIndex = selectedTrackIndex(
    capabilities.audio_tracks,
    capabilities.default_audio_stream_index,
  );
  if (capabilities.subtitles_forced_off) {
    return { audioStreamIndex, subtitleStreamIndex: "" as const, subtitlesEnabled: false };
  }
  const subtitleStreamIndex = selectedTrackIndex(
    capabilities.subtitle_tracks,
    capabilities.default_subtitle_stream_index,
  );
  return {
    audioStreamIndex,
    subtitleStreamIndex,
    subtitlesEnabled: subtitleStreamIndex !== "",
  };
}
