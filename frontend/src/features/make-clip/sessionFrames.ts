import type { PlexSession } from "../../types";

export interface SessionFrameCapture {
  mediaIdentity: string;
  positionMs: number;
  playbackState: string;
  captureVersion: number;
}

export type SessionFrameCaptures = Record<string, SessionFrameCapture>;

export function reconcileSessionFrameCaptures(
  previous: SessionFrameCaptures,
  sessions: PlexSession[],
): SessionFrameCaptures {
  const next: SessionFrameCaptures = {};
  for (const session of sessions) {
    const existing = previous[session.session_identity];
    const playbackState = session.state.toLowerCase();
    const positionMs = Math.max(0, Math.round(session.position_ms));
    if (!existing || existing.mediaIdentity !== session.media_identity) {
      next[session.session_identity] = {
        mediaIdentity: session.media_identity,
        positionMs,
        playbackState,
        captureVersion: (existing?.captureVersion ?? 0) + 1,
      };
      continue;
    }

    const isPaused = playbackState === "paused";
    const resumedPause = isPaused && existing.playbackState !== "paused";
    const movedWhilePaused = isPaused && existing.positionMs !== positionMs;
    next[session.session_identity] = {
      ...existing,
      playbackState,
      ...(resumedPause || movedWhilePaused
        ? { positionMs, captureVersion: existing.captureVersion + 1 }
        : {}),
    };
  }
  return next;
}
