import type {
  ApplicationSettings,
  ApplicationSettingsUpdate,
  ClipCreateRequest,
  HealthResponse,
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
  const response = await fetch("/api/health", {
    headers: { Accept: "application/json" },
  });
  return parseResponse<HealthResponse>(response, "Health request");
}

export async function fetchSettings(): Promise<ApplicationSettings> {
  const response = await fetch("/api/settings", {
    headers: { Accept: "application/json" },
  });
  return parseResponse<ApplicationSettings>(response, "Settings request");
}

export async function updateSettings(
  update: ApplicationSettingsUpdate,
): Promise<ApplicationSettings> {
  const response = await fetch("/api/settings", {
    method: "PUT",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  return parseResponse<ApplicationSettings>(response, "Settings update");
}

export async function testPlexConnection(
  connection: PlexConnectionRequest,
): Promise<PlexConnectionResult> {
  const response = await fetch("/api/settings/plex/test", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(connection),
  });
  return parseResponse<PlexConnectionResult>(response, "Plex connection test");
}

export async function fetchPlexSessions(): Promise<PlexSessionSnapshot> {
  const response = await fetch("/api/sessions", {
    headers: { Accept: "application/json" },
  });
  return parseResponse<PlexSessionSnapshot>(response, "Plex sessions request");
}

export async function fetchMediaCapabilities(sessionIdentity: string): Promise<MediaCapabilities> {
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionIdentity)}/media-capabilities`,
    {
      headers: { Accept: "application/json" },
    },
  );
  return parseResponse<MediaCapabilities>(response, "Media capabilities request");
}

export async function createClip(request: ClipCreateRequest): Promise<JobSnapshot> {
  const response = await fetch("/api/clips", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  return parseResponse<JobSnapshot>(response, "Clip request");
}

export async function fetchJob(jobId: string): Promise<JobSnapshot> {
  const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, {
    headers: { Accept: "application/json" },
  });
  return parseResponse<JobSnapshot>(response, "Job request");
}
