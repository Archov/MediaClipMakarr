import type {
  ApplicationSettings,
  ApplicationSettingsUpdate,
  ClipCreateRequest,
  ClipMetadataUpdate,
  ClipPage,
  ClipRecord,
  ClipDeleteResult,
  ClipFilterOptions,
  HealthResponse,
  ImmichAssetCheckResult,
  ImmichAssetDeleteResult,
  ImmichConnectionRequest,
  ImmichConnectionResult,
  JobSnapshot,
  MediaCapabilities,
  PlexConnectionRequest,
  PlexConnectionResult,
  PlexSessionSnapshot,
  StructuredError,
} from "./types";

export class ApiRequestError extends Error {
  readonly detail: StructuredError | null;

  constructor(message: string, detail: StructuredError | null = null) {
    super(message);
    this.name = "ApiRequestError";
    this.detail = detail;
  }
}

function structuredError(value: unknown): StructuredError | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const detail = value as Record<string, unknown>;
  if (typeof detail.code !== "string" || typeof detail.message !== "string") return null;
  return {
    code: detail.code,
    message: detail.message,
    retryable: detail.retryable === true,
    alternatives: Array.isArray(detail.alternatives)
      ? detail.alternatives.filter(
          (item): item is Record<string, unknown> => Boolean(item) && typeof item === "object",
        )
      : [],
    context:
      detail.context && typeof detail.context === "object" && !Array.isArray(detail.context)
        ? detail.context as Record<string, unknown>
        : {},
  };
}

async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch {
    throw new ApiRequestError("Could not reach the server. Check that it's running and try again.");
  }
}

async function parseResponse<T>(response: Response, action: string): Promise<T> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as
      | {
          detail?:
            | string
            | { code?: string; message?: string }
            | Array<{ loc?: Array<string | number>; msg?: string }>;
        }
      | null;
    const detail = payload?.detail;
    const message = Array.isArray(detail)
      ? detail
          .map((issue) => {
            const location = (issue.loc ?? [])
              .filter((part) => part !== "body")
              .map((part) => (typeof part === "number" ? part + 1 : part.replaceAll("_", " ")))
              .join(" › ");
            const reason = issue.msg?.replace(/^Value error,\s*/i, "") ?? "Invalid value.";
            return location ? `${location}: ${reason}` : reason;
          })
          .join(" ")
      : typeof detail === "string"
        ? detail
        : detail?.message
          ? detail.code
            ? `${detail.message} (${detail.code})`
            : detail.message
          : undefined;
    throw new ApiRequestError(
      message ?? `${action} failed with HTTP ${response.status}.`,
      structuredError(!Array.isArray(detail) && typeof detail === "object" ? detail : null),
    );
  }
  return (await response.json()) as T;
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await apiFetch("/api/health", {
    headers: { Accept: "application/json" },
  });
  return parseResponse<HealthResponse>(response, "Health request");
}

export async function fetchSettings(): Promise<ApplicationSettings> {
  const response = await apiFetch("/api/settings", {
    headers: { Accept: "application/json" },
  });
  return parseResponse<ApplicationSettings>(response, "Settings request");
}

export async function updateSettings(
  update: ApplicationSettingsUpdate,
): Promise<ApplicationSettings> {
  const response = await apiFetch("/api/settings", {
    method: "PUT",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  return parseResponse<ApplicationSettings>(response, "Settings update");
}

export async function testPlexConnection(
  connection: PlexConnectionRequest,
): Promise<PlexConnectionResult> {
  const response = await apiFetch("/api/settings/plex/test", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(connection),
  });
  return parseResponse<PlexConnectionResult>(response, "Plex connection test");
}

export async function testImmichConnection(
  connection: ImmichConnectionRequest,
): Promise<ImmichConnectionResult> {
  const response = await apiFetch("/api/settings/immich/test", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(connection),
  });
  return parseResponse<ImmichConnectionResult>(response, "Immich connection test");
}

export async function fetchPlexSessions(): Promise<PlexSessionSnapshot> {
  const response = await apiFetch("/api/sessions", {
    headers: { Accept: "application/json" },
  });
  return parseResponse<PlexSessionSnapshot>(response, "Plex sessions request");
}

export async function fetchMediaCapabilities(sessionIdentity: string): Promise<MediaCapabilities> {
  const response = await apiFetch(
    `/api/sessions/${encodeURIComponent(sessionIdentity)}/media-capabilities`,
    {
      headers: { Accept: "application/json" },
    },
  );
  return parseResponse<MediaCapabilities>(response, "Media capabilities request");
}

export function sessionFrameUrl(
  sessionIdentity: string,
  mediaIdentity: string,
  positionMs: number,
  captureVersion: number,
  download = false,
): string {
  const params = new URLSearchParams({
    media_identity: mediaIdentity,
    position_ms: String(positionMs),
    v: String(captureVersion),
  });
  if (download) params.set("download", "true");
  return `/api/sessions/${encodeURIComponent(sessionIdentity)}/frame?${params}`;
}

export async function createClip(request: ClipCreateRequest): Promise<JobSnapshot> {
  const response = await apiFetch("/api/clips", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  return parseResponse<JobSnapshot>(response, "Clip request");
}

export async function fetchJob(jobId: string): Promise<JobSnapshot> {
  const response = await apiFetch(`/api/jobs/${encodeURIComponent(jobId)}`, {
    headers: { Accept: "application/json" },
  });
  return parseResponse<JobSnapshot>(response, "Job request");
}

export interface ClipQuery {
  page?: number;
  pageSize?: number | "all";
  search?: string;
  library?: string;
  mediaType?: string;
  media?: string[];
  episode?: string[];
  sort?: string;
}

export async function fetchClips(query: ClipQuery): Promise<ClipPage> {
  const params = new URLSearchParams();
  params.set("page", String(query.page ?? 1));
  if (query.pageSize === "all") params.set("all", "true");
  else params.set("page_size", String(query.pageSize ?? 25));
  if (query.search) params.set("search", query.search);
  if (query.library) params.set("library", query.library);
  if (query.mediaType) params.set("media_type", query.mediaType);
  query.media?.forEach((value) => params.append("media", value));
  query.episode?.forEach((value) => params.append("episode", value));
  if (query.sort) params.set("sort", query.sort);
  const response = await apiFetch(`/api/clips?${params}`, { headers: { Accept: "application/json" } });
  return parseResponse<ClipPage>(response, "Clip library request");
}

export async function fetchClip(clipId: string): Promise<ClipRecord> {
  const response = await apiFetch(`/api/clips/${encodeURIComponent(clipId)}`, {
    headers: { Accept: "application/json" },
  });
  return parseResponse<ClipRecord>(response, "Clip detail request");
}

export async function fetchClipLibraries(): Promise<string[]> {
  const response = await apiFetch("/api/clips/libraries", {
    headers: { Accept: "application/json" },
  });
  return parseResponse<string[]>(response, "Clip libraries request");
}

export async function fetchClipFilterOptions(): Promise<ClipFilterOptions> {
  const response = await apiFetch("/api/clips/filter-options", {
    headers: { Accept: "application/json" },
  });
  return parseResponse<ClipFilterOptions>(response, "Clip filter options request");
}

export async function updateClipMetadata(
  clipId: string,
  update: ClipMetadataUpdate,
): Promise<JobSnapshot> {
  const response = await apiFetch(`/api/clips/${encodeURIComponent(clipId)}`, {
    method: "PUT",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  return parseResponse<JobSnapshot>(response, "Clip metadata update");
}

export async function uploadClipToImmich(clipId: string): Promise<JobSnapshot> {
  const response = await apiFetch(`/api/clips/${encodeURIComponent(clipId)}/immich-upload`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
  });
  return parseResponse<JobSnapshot>(response, "Immich upload request");
}

export async function bulkUploadClipsToImmich(): Promise<JobSnapshot> {
  const response = await apiFetch("/api/clips/immich-upload/bulk", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
  });
  return parseResponse<JobSnapshot>(response, "Bulk Immich upload request");
}

export async function deleteClip(
  clipId: string,
  expectedRevision: number,
  deleteFromImmich = false,
): Promise<ClipDeleteResult> {
  const response = await apiFetch(`/api/clips/${encodeURIComponent(clipId)}`, {
    method: "DELETE",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({
      expected_revision: expectedRevision,
      delete_from_immich: deleteFromImmich,
    }),
  });
  return parseResponse<ClipDeleteResult>(response, "Clip deletion");
}

export async function checkImmichAsset(clipId: string): Promise<ImmichAssetCheckResult> {
  const response = await apiFetch(`/api/clips/${encodeURIComponent(clipId)}/immich-check`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
  });
  return parseResponse<ImmichAssetCheckResult>(response, "Immich asset check");
}

export async function reuploadClipToImmich(clipId: string): Promise<JobSnapshot> {
  const response = await apiFetch(`/api/clips/${encodeURIComponent(clipId)}/immich-reupload`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
  });
  return parseResponse<JobSnapshot>(response, "Immich reupload request");
}

export async function retryImmichAssetDelete(assetId: string): Promise<ImmichAssetDeleteResult> {
  const response = await apiFetch(`/api/immich/assets/${encodeURIComponent(assetId)}/delete-retry`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
  });
  return parseResponse<ImmichAssetDeleteResult>(response, "Immich asset delete retry");
}
