import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { fetchJob, requestClipGif } from "../../api";
import type { GifExportRange } from "../../api";
import type { GifJobResult } from "../../types";

const NONTERMINAL_JOB_STATES = new Set(["QUEUED", "RUNNING", "FINALIZING"]);

function triggerDownload(url: string) {
  const link = document.createElement("a");
  link.href = url;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function isGifResult(result: unknown): result is GifJobResult {
  return Boolean(result) && typeof result === "object" && "gif_url" in (result as object);
}

/** Trigger a share-sized GIF export for `clipId` and download it once ready —
 * used by the create-clip, library, and trim-clip screens. Pass `range` (the
 * trim editor's current selection) to export just that sub-range without
 * touching the persisted clip file; omit it to export the whole clip. A
 * cache hit resolves (and downloads) immediately; a miss polls the enqueued
 * job. */
export function useGifExport(clipId: string | null) {
  const [jobId, setJobId] = useState<string | null>(null);
  // The POST can resolve after the caller has already moved on to a
  // different clip (e.g. the trim dialog reopened against clip B while
  // clip A's export was still in flight) — a ref (not state) lets onSuccess
  // read the *current* clipId without depending on the render that scheduled
  // the mutation.
  const latestClipId = useRef(clipId);
  latestClipId.current = clipId;

  const requestMutation = useMutation({
    mutationFn: ({ id, range }: { id: string; range?: GifExportRange }) =>
      requestClipGif(id, range),
    onSuccess: (response, variables) => {
      if (variables.id !== latestClipId.current) return;
      if (response.status === "cached" && response.gif_url) {
        triggerDownload(response.gif_url);
        setJobId(null);
      } else if (response.job) {
        setJobId(response.job.id);
      }
    },
  });

  const job = useQuery({
    queryKey: ["job", "gif", jobId],
    queryFn: () => fetchJob(jobId!),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state && NONTERMINAL_JOB_STATES.has(state) ? 1_000 : false;
    },
  });

  useEffect(() => {
    if (job.data?.state === "SUCCEEDED" && isGifResult(job.data.result)) {
      triggerDownload(job.data.result.gif_url);
      setJobId(null);
    }
  }, [job.data]);

  useEffect(() => {
    setJobId(null);
    requestMutation.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clipId]);

  const jobState = job.data?.state;
  const busy =
    requestMutation.isPending ||
    (Boolean(jobId) &&
      (job.isPending || (jobState !== undefined && NONTERMINAL_JOB_STATES.has(jobState))));
  const error =
    requestMutation.error?.message ??
    job.error?.message ??
    (jobState === "FAILED" ? job.data?.error?.message ?? "GIF export failed." : null);

  return {
    exportGif: (range?: GifExportRange) => {
      if (clipId) requestMutation.mutate({ id: clipId, range });
    },
    busy,
    error,
  };
}
