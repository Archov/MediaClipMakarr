import assert from "node:assert/strict";
import test from "node:test";

import type { PlexSession } from "../src/types.ts";
import {
  reconcileSessionFrameCaptures,
  type SessionFrameCaptures,
} from "../src/features/make-clip/sessionFrames.ts";

function session(state: string, positionMs: number, mediaIdentity = "media-1"): PlexSession {
  return {
    session_identity: "session-1",
    media_identity: mediaIdentity,
    title: "Movie",
    media_type: "movie",
    plex_user: null,
    player: null,
    state,
    position_ms: positionMs,
    duration_ms: 60_000,
    sampled_at: "2026-09-02T00:00:00Z",
    plex_rating_key: null,
    plex_media_key: null,
    plex_part_id: null,
    plex_part_key: null,
    selected_audio_streams: [],
    selected_subtitle_streams: [],
  };
}

function update(previous: SessionFrameCaptures, current: PlexSession): SessionFrameCaptures {
  return reconcileSessionFrameCaptures(previous, [current]);
}

test("freezes a session frame while playback advances and refreshes when paused", () => {
  let captures = update({}, session("paused", 1_000));
  const initial = captures["session-1"];

  captures = update(captures, session("playing", 2_000));
  assert.equal(captures["session-1"].positionMs, 1_000);
  assert.equal(captures["session-1"].captureVersion, initial.captureVersion);

  captures = update(captures, session("playing", 7_000));
  assert.equal(captures["session-1"].positionMs, 1_000);

  captures = update(captures, session("buffering", 7_500));
  assert.equal(captures["session-1"].positionMs, 1_000);

  captures = update(captures, session("paused", 8_000));
  assert.equal(captures["session-1"].positionMs, 8_000);
  assert.equal(captures["session-1"].captureVersion, initial.captureVersion + 1);
});

test("captures a new frame when the session changes media", () => {
  const captures = update(update({}, session("playing", 1_000)), session("playing", 4_000, "media-2"));
  assert.equal(captures["session-1"].mediaIdentity, "media-2");
  assert.equal(captures["session-1"].positionMs, 4_000);
});
