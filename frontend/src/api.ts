import type {
  ApplicationSettings,
  ApplicationSettingsUpdate,
  HealthResponse,
  PlexConnectionResult,
} from "./types";

async function parseResponse<T>(response: Response, action: string): Promise<T> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as
      | { detail?: string | { message?: string } }
      | null;
    const detail = payload?.detail;
    const message = typeof detail === "string" ? detail : detail?.message;
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

export async function testPlexConnection(): Promise<PlexConnectionResult> {
  const response = await fetch("/api/settings/plex/test", {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  return parseResponse<PlexConnectionResult>(response, "Plex connection test");
}
