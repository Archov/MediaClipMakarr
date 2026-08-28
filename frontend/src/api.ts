import type {
  ApplicationSettings,
  ApplicationSettingsUpdate,
  HealthResponse,
  PlexConnectionRequest,
  PlexConnectionResult,
  PlexSessionSnapshot,
} from "./types";

async function parseResponse<T>(response: Response, action: string): Promise<T> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as
      | {
          detail?:
            | string
            | { message?: string }
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
        : detail?.message;
    throw new Error(message ?? `${action} failed with HTTP ${response.status}.`);
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
