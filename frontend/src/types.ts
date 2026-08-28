export type HealthStatus = "ok" | "degraded" | "error";

export interface ComponentHealth {
  status: HealthStatus;
  message: string;
  details: Record<string, string | boolean | number>;
}

export interface DirectoryHealth {
  name: string;
  mode: "read-write" | "read-only";
  status: HealthStatus;
  message: string;
}

export interface HealthResponse {
  status: HealthStatus;
  application: ComponentHealth;
  database: ComponentHealth;
  media_tools: ComponentHealth;
  directories: DirectoryHealth[];
}

export interface SourcePathMapping {
  plex_prefix: string;
  local_prefix: string;
}

export type ApplicationSettingField =
  | "plex_url"
  | "plex_token"
  | "source_path_mappings"
  | "timezone"
  | "x264_preset";

export interface ApplicationSettings {
  plex_url: string;
  plex_token_configured: boolean;
  source_path_mappings: SourcePathMapping[];
  timezone: string;
  timezone_configured: boolean;
  available_timezones: string[];
  x264_preset: string;
  environment_managed: Record<ApplicationSettingField, boolean>;
}

export interface ApplicationSettingsUpdate {
  plex_url?: string;
  plex_token?: string;
  clear_plex_token?: boolean;
  source_path_mappings?: SourcePathMapping[];
  timezone?: string;
  x264_preset?: string;
}

export interface PlexConnectionResult {
  connected: boolean;
  code: string;
  message: string;
  server_name: string | null;
  server_version: string | null;
}

export interface PlexConnectionRequest {
  plex_url?: string;
  plex_token?: string;
}

export type PlexSessionSnapshotStatus =
  | "ok"
  | "not_configured"
  | "invalid_url"
  | "invalid_token"
  | "http_error"
  | "invalid_response"
  | "unreachable"
  | "error";

export interface PlexSession {
  session_identity: string;
  media_identity: string;
  title: string;
  media_type: string;
  plex_user: string | null;
  player: string | null;
  state: string;
  position_ms: number;
  duration_ms: number | null;
  sampled_at: string;
  plex_rating_key: string | null;
  plex_media_key: string | null;
  plex_part_id: string | null;
}

export interface PlexSessionSnapshot {
  status: PlexSessionSnapshotStatus;
  message: string;
  sampled_at: string;
  sessions: PlexSession[];
}
