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
  | "x264_preset"
  | "immich_url"
  | "immich_api_key"
  | "immich_default_tag"
  | "immich_auto_upload"
  | "immich_manage_remote"
  | "immich_tag_library"
  | "immich_tag_show"
  | "immich_tag_episode";

export interface ApplicationSettings {
  plex_url: string;
  plex_token_configured: boolean;
  source_path_mappings: SourcePathMapping[];
  timezone: string;
  timezone_configured: boolean;
  available_timezones: string[];
  x264_preset: string;
  immich_url: string;
  immich_api_key_configured: boolean;
  immich_default_tag: string;
  immich_auto_upload: boolean;
  immich_manage_remote: boolean;
  immich_tag_library: boolean;
  immich_tag_show: boolean;
  immich_tag_episode: boolean;
  environment_managed: Record<ApplicationSettingField, boolean>;
}

export interface ApplicationSettingsUpdate {
  plex_url?: string;
  plex_token?: string;
  clear_plex_token?: boolean;
  source_path_mappings?: SourcePathMapping[];
  timezone?: string;
  x264_preset?: string;
  immich_url?: string;
  immich_api_key?: string;
  clear_immich_api_key?: boolean;
  immich_default_tag?: string;
  immich_auto_upload?: boolean;
  immich_manage_remote?: boolean;
  immich_tag_library?: boolean;
  immich_tag_show?: boolean;
  immich_tag_episode?: boolean;
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

export interface ImmichConnectionResult {
  connected: boolean;
  code: string;
  message: string;
  server_version: string | null;
  api_key_permissions: string[] | null;
}

export interface ImmichConnectionRequest {
  immich_url?: string;
  immich_api_key?: string;
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
  selected_audio_streams: PlexPartStream[];
  selected_subtitle_streams: PlexPartStream[];
}

export interface PlexPartStream {
  id: string | null;
  key: string | null;
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
  alternatives: Array<Record<string, unknown>>;
  context: Record<string, unknown>;
}

export interface ClipCreateRequest {
  session_identity: string;
  media_identity: string;
  start_ms: number;
  end_ms: number;
  audio_stream_index?: number | null;
  subtitle_stream_index?: number | null;
  subtitles_enabled?: boolean;
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

export type TrackKind = "video" | "audio" | "subtitle" | "attachment";
export type SubtitleKind = "text" | "bitmap" | "unsupported";

export interface TrackDescriptor {
  kind: TrackKind;
  stream_index: number | null;
  plex_track_id: string | null;
  plex_key: string | null;
  codec: string | null;
  language: string | null;
  title: string | null;
  selected: boolean;
  available: boolean;
  unavailable_reason: string | null;
  subtitle_kind: SubtitleKind | null;
  external: boolean;
}

export interface HdrCapabilities {
  hdr10: boolean;
  hlg: boolean;
  dolby_vision: boolean;
  dolby_vision_profile: number | null;
  dolby_vision_base_layer_compatible: boolean | null;
  dolby_vision_bl_compatibility_id: number | null;
  color: VideoColorMetadata;
  probe_context: Record<string, unknown>;
}

export interface MediaCapabilities {
  duration_ms: number | null;
  frame_rate: number | null;
  video_tracks: TrackDescriptor[];
  audio_tracks: TrackDescriptor[];
  subtitle_tracks: TrackDescriptor[];
  attachment_tracks: TrackDescriptor[];
  default_audio_stream_index: number;
  default_subtitle_stream_index: number | null;
  subtitles_forced_off: boolean;
  hdr: HdrCapabilities;
  warnings: string[];
}

export interface ResolvedSourceMedia {
  plex_path: string;
  local_path: string;
  fingerprint: SourceFingerprint;
  duration_ms: number | null;
  video_streams: VideoStreamIdentity[];
  audio_streams: MediaStreamIdentity[];
  subtitle_streams: MediaStreamIdentity[];
  attachment_streams: MediaStreamIdentity[];
  capabilities: MediaCapabilities | null;
  selected_audio_stream: MediaStreamIdentity;
  selected_subtitle: {
    enabled: boolean;
    stream: MediaStreamIdentity | null;
    strategy: "off" | "embedded_text" | "external_text" | "bitmap";
    external_url: string | null;
  };
  subtitles_forced_off: boolean;
}

export type JobState = "QUEUED" | "RUNNING" | "FINALIZING" | "SUCCEEDED" | "PARTIAL" | "FAILED";

export type JobStage =
  | "queued"
  | "validating"
  | "rendering"
  | "generating_thumbnail"
  | "updating_metadata"
  | "uploading_asset"
  | "setting_description"
  | "applying_tags"
  | "finalizing"
  | "complete"
  | "failed";

export interface ClipJobResult {
  clip_id: string;
  title: string;
  file_path: string;
  duration_ms: number;
  play_url: string;
  download_url: string;
}

export interface ImmichUploadJobResult {
  clip_id: string;
  immich_asset_id: string;
  description_set: boolean;
  tags_applied: string[];
}

export interface BulkImmichUploadJobResultDetail {
  clip_id: string;
  title: string | null;
  stage: "validate" | "upload";
  outcome: "succeeded" | "partial" | "failed" | "skipped";
  error_code: string | null;
}

export interface BulkImmichUploadJobResult {
  total: number;
  succeeded: number;
  partial: number;
  failed: number;
  skipped: number;
  reuploaded: number;
  permission_warnings: string[];
  details: BulkImmichUploadJobResultDetail[];
}

export interface JobSnapshot {
  id: string;
  type:
    | "clip_create"
    | "thumbnail_generate"
    | "clip_metadata_edit"
    | "immich_upload"
    | "bulk_immich_upload";
  state: JobState;
  stage: JobStage;
  progress: number;
  current_stage_progress: number;
  elapsed_ms: number | null;
  queue_position: number | null;
  message: string;
  result: ClipJobResult | ImmichUploadJobResult | BulkImmichUploadJobResult | null;
  error: StructuredError | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

// A lean, read-only view of a clip's latest Immich-upload job, embedded directly in
// the clip list/detail response (see ClipRecord.immich_upload_job) rather than
// fetched per-clip.
export interface ImmichUploadJobSummary {
  id: string;
  state: JobState;
  stage: JobStage;
  progress: number;
  message: string;
  result: ImmichUploadJobResult | null;
  error: StructuredError | null;
}

export interface ClipRecord {
  id: string;
  title: string;
  custom_title: string | null;
  library: string;
  media_type: string;
  duration_ms: number;
  revision: number;
  movie_title: string | null;
  movie_year: number | null;
  show_name: string | null;
  episode_title: string | null;
  season_number: number | null;
  episode_number: number | null;
  clip_number: number;
  plex_username: string | null;
  source_start_ms: number;
  source_end_ms: number;
  created_at: string;
  updated_at: string;
  thumbnail_url: string;
  play_url: string;
  download_url: string;
  immich_asset_id: string | null;
  immich_upload_job: ImmichUploadJobSummary | null;
}

export interface ClipPage {
  items: ClipRecord[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
}

export interface ClipFilterOptions {
  libraries: string[];
  movies: string[];
  shows: string[];
  episodes: Array<{
    show_name: string;
    title: string;
    season_number: number | null;
    episode_number: number | null;
  }>;
}

export interface ClipMetadataUpdate {
  expected_revision: number;
  custom_title?: string | null;
  library?: string | null;
  media_type?: "movie" | "episode" | "video" | null;
  movie_title?: string | null;
  movie_year?: number | null;
  show_name?: string | null;
  episode_title?: string | null;
  season_number?: number | null;
  episode_number?: number | null;
}

export interface ImmichDeleteMissingPermission {
  retry_token: string;
  settings_url: string;
}

export interface ClipDeleteResult {
  id: string;
  title: string;
  deleted: boolean;
  cleanup_warnings: string[];
  immich_delete_missing_permission: ImmichDeleteMissingPermission | null;
}

export interface ImmichAssetCheckResult {
  status: "ok" | "missing_permission" | "asset_missing";
  open_url: string | null;
  settings_url: string | null;
}

export interface ImmichAssetDeleteResult {
  status: "ok" | "missing_permission";
  settings_url: string | null;
}
