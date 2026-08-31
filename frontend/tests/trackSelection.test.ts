import assert from "node:assert/strict";
import test from "node:test";

import { initialTrackSelection } from "../src/features/make-clip/trackSelection.ts";
import type { TrackDescriptor } from "../src/types.ts";

function track(
  kind: "audio" | "subtitle",
  streamIndex: number,
  selected = false,
): TrackDescriptor {
  return {
    kind,
    stream_index: streamIndex,
    plex_track_id: null,
    plex_key: null,
    codec: kind === "audio" ? "aac" : "subrip",
    language: "eng",
    title: null,
    selected,
    available: true,
    unavailable_reason: null,
    subtitle_kind: kind === "subtitle" ? "text" : null,
    external: false,
  };
}

test("Plex subtitles off stays off even when subtitle tracks are available", () => {
  const selection = initialTrackSelection({
    audio_tracks: [track("audio", 1, true)],
    subtitle_tracks: [track("subtitle", 2), track("subtitle", 3)],
    default_audio_stream_index: 1,
    default_subtitle_stream_index: null,
    subtitles_forced_off: true,
  });

  assert.deepEqual(selection, {
    audioStreamIndex: 1,
    subtitleStreamIndex: "",
    subtitlesEnabled: false,
  });
});

test("the Plex-selected subtitle remains the initial subtitle", () => {
  const selection = initialTrackSelection({
    audio_tracks: [track("audio", 1, true)],
    subtitle_tracks: [track("subtitle", 2), track("subtitle", 3, true)],
    default_audio_stream_index: 1,
    default_subtitle_stream_index: 3,
    subtitles_forced_off: false,
  });

  assert.equal(selection.subtitleStreamIndex, 3);
  assert.equal(selection.subtitlesEnabled, true);
});
