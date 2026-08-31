import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { fetchJob, fetchPlexSessions } from "../../api";
import type { JobSnapshot, PlexSession, PlexSessionSnapshot } from "../../types";

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
  const sampledAt = Date.parse(session.sampled_at);
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

