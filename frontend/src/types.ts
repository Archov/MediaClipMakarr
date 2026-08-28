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
  plex_part_key: string | null;
  plex_part_file: string | null;
  selected_audio_streams: PlexPartStream[];
}

export interface PlexPartStream {
  id: string | null;
  stream_index: number | null;
  stream_type: number | null;
  codec: string | null;
  language: string | null;
  title: string | null;
  selected: boolean;
}

export interface PlexSessionSnapshot {
  status: PlexSessionSnapshotStatus;
  message: string;
  sampled_at: string;
  sessions: PlexSession[];
}

export interface StructuredError {
  code: string;
  message: string;
  retryable: boolean;
}

export interface ClipCreateRequest {
  session_identity: string;
  media_identity: string;
  start_ms: number;
  end_ms: number;
}

export interface SourceFingerprint {
  size_bytes: number;
  modified_at: string;
}

export interface VideoColorMetadata {
  color_space: string | null;
  color_transfer: string | null;
  color_primaries: string | null;
  color_range: string | null;
}

export interface MediaStreamIdentity {
  stream_index: number;
  codec_type: string;
  codec_name: string | null;
  language: string | null;
  title: string | null;
}

export interface VideoStreamIdentity extends MediaStreamIdentity {
  width: number | null;
  height: number | null;
  color: VideoColorMetadata;
}

export interface ResolvedSourceMedia {
  plex_path: string;
  local_path: string;
  fingerprint: SourceFingerprint;
  duration_ms: number | null;
  video_streams: VideoStreamIdentity[];
  audio_streams: MediaStreamIdentity[];
  subtitle_streams: MediaStreamIdentity[];
  selected_audio_stream: MediaStreamIdentity;
  subtitles_forced_off: boolean;
}

export type JobState = "QUEUED" | "RUNNING" | "FINALIZING" | "SUCCEEDED" | "PARTIAL" | "FAILED";

export type JobStage = "queued" | "validating" | "rendering" | "finalizing" | "complete" | "failed";

export interface ClipJobResult {
  clip_id: string;
  title: string;
  file_path: string;
  duration_ms: number;
  play_url: string;
  download_url: string;
}

export interface JobSnapshot {
  id: string;
  type: "clip_create";
  state: JobState;
  stage: JobStage;
  progress: number;
  current_stage_progress: number;
  elapsed_ms: number | null;
  queue_position: number | null;
  message: string;
  result: ClipJobResult | null;
  error: StructuredError | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}
