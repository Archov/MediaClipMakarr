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
