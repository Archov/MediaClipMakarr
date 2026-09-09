import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import {
  detectCrop,
  fetchAspectRatioOverride,
  fetchJob,
  fetchMediaCapabilities,
  fetchPlexSessions,
} from "../../api";
import { parseUtcMs } from "../../timestamps";
import type { CropBox, JobSnapshot, PlexSession, PlexSessionSnapshot } from "../../types";
import { computeRenderDurationMs, isRenderingJob } from "./renderDuration";

export function useClock(enabled: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [enabled]);
  return now;
}

export function displayedPosition(session: PlexSession, now: number): number {
  if (session.state.toLowerCase() !== "playing") return session.position_ms;
  const sampledAt = parseUtcMs(session.sampled_at);
  if (!Number.isFinite(sampledAt)) return session.position_ms;
  const extrapolated = session.position_ms + Math.max(0, now - sampledAt);
  return session.duration_ms === null ? extrapolated : Math.min(session.duration_ms, extrapolated);
}

export function useLivePlexSessions() {
  const queryClient = useQueryClient();
  const snapshotRevision = useRef(0);
  const sessions = useQuery({
    queryKey: ["plex-sessions"],
    queryFn: async () => {
      const requestRevision = snapshotRevision.current;
      const snapshot = await fetchPlexSessions();
      if (requestRevision === snapshotRevision.current) return snapshot;
      return queryClient.getQueryData<PlexSessionSnapshot>(["plex-sessions"]) ?? snapshot;
    },
    refetchInterval: 15_000,
  });

  useEffect(() => {
    if (typeof EventSource === "undefined") return undefined;
    const eventSource = new EventSource("/api/sessions/events");
    const handleSnapshot = (event: MessageEvent<string>) => {
      const snapshot = JSON.parse(event.data) as PlexSessionSnapshot;
      snapshotRevision.current += 1;
      queryClient.setQueryData(["plex-sessions"], snapshot);
    };
    const handleError = () => {
      void queryClient.invalidateQueries({ queryKey: ["plex-sessions"] });
    };
    eventSource.addEventListener("snapshot", handleSnapshot as EventListener);
    eventSource.addEventListener("error", handleError);
    return () => {
      eventSource.removeEventListener("snapshot", handleSnapshot as EventListener);
      eventSource.removeEventListener("error", handleError);
      eventSource.close();
    };
  }, [queryClient]);

  return sessions;
}

export interface SessionCropPreview {
  crop: CropBox | null;
  /** The box a preview thumbnail should render at: the crop's own ratio, or
   * else the source's native ratio — never an assumed 16:9. A cinematic-ratio
   * source (e.g. 2.39:1) shown in a 16:9 box gets padded by `object-fit:
   * contain` into fake letterbox bars that aren't in the actual frame or the
   * rendered clip, even though nothing was ever cropped. */
  aspectRatio: string;
}

/** The crop box for a show/movie's saved aspect-ratio override (or `null` if
 * none is set) plus the aspect ratio a preview thumbnail should render at.
 * The override needs no time range to compute, only the source's
 * dimensions, so this works before Start/End (or even a session selection
 * for a clip) has been captured. Used to crop live "stream" preview
 * thumbnails and the boundary-editor Start preview ahead of any detection. */
export function useSavedCropForSession(
  sessionIdentity: string,
  mediaIdentity: string,
): SessionCropPreview {
  const [state, setState] = useState<SessionCropPreview>({ crop: null, aspectRatio: "16 / 9" });

  useEffect(() => {
    let cancelled = false;
    setState({ crop: null, aspectRatio: "16 / 9" });

    fetchMediaCapabilities(sessionIdentity)
      .then((capabilities) => {
        if (cancelled || !capabilities.width || !capabilities.height) return;
        setState((current) =>
          current.crop
            ? current
            : { crop: null, aspectRatio: `${capabilities.width} / ${capabilities.height}` },
        );
      })
      .catch(() => {
        // Capabilities unavailable — keep the 16:9 fallback.
      });

    fetchAspectRatioOverride(sessionIdentity)
      .then((override) => {
        if (cancelled || !override.aspect_ratio) return null;
        return detectCrop(sessionIdentity, {
          start_ms: 0,
          end_ms: 1,
          aspect_ratio: override.aspect_ratio,
        });
      })
      .then((result) => {
        if (cancelled || !result?.crop) return;
        const { crop } = result;
        setState({ crop, aspectRatio: `${crop.width} / ${crop.height}` });
      })
      .catch(() => {
        // No saved override, or Plex/lookup unavailable — no crop.
      });

    return () => {
      cancelled = true;
    };
  }, [sessionIdentity, mediaIdentity]);

  return state;
}

/** The elapsed render time to display for `job`: live and ticking while it's
 * still rendering, frozen at the final total once it succeeds. See
 * `computeRenderDurationMs` for the underlying (independently testable)
 * logic. */
export function useRenderDuration(job: JobSnapshot | null): number | null {
  const now = useClock(isRenderingJob(job));
  return computeRenderDurationMs(job, now);
}

export function useJobSnapshot(
  initialJob: JobSnapshot | null,
  rememberedJobId: string | null = null,
) {
  const [job, setJob] = useState<JobSnapshot | null>(initialJob);

  useEffect(() => {
    const jobId = initialJob?.id ?? rememberedJobId;
    if (!jobId) {
      setJob(null);
      return undefined;
    }
    if (initialJob?.id === jobId) setJob(initialJob);
    if (typeof EventSource === "undefined") return undefined;

    let closed = false;
    let snapshotRevision = 0;
    let fallbackRequest = 0;
    const eventSource = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/events`);
    const handleSnapshot = (event: MessageEvent<string>) => {
      snapshotRevision += 1;
      setJob(JSON.parse(event.data) as JobSnapshot);
    };
    const handleError = () => {
      if (!closed) {
        const request = ++fallbackRequest;
        const requestRevision = snapshotRevision;
        void fetchJob(jobId)
          .then((snapshot) => {
            if (
              !closed &&
              request === fallbackRequest &&
              requestRevision === snapshotRevision &&
              snapshot.id === jobId
            ) {
              setJob(snapshot);
            }
          })
          .catch(() => undefined);
      }
    };

    eventSource.addEventListener("snapshot", handleSnapshot as EventListener);
    eventSource.addEventListener("error", handleError);
    if (!initialJob || initialJob.id !== jobId) handleError();
    return () => {
      closed = true;
      eventSource.removeEventListener("snapshot", handleSnapshot as EventListener);
      eventSource.removeEventListener("error", handleError);
      eventSource.close();
    };
  }, [initialJob, rememberedJobId]);

  return job;
}

