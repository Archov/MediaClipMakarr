from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mediaclipmakarr.render_plan import resolve_unique_clip_path, sanitize_display_name
from mediaclipmakarr.subprocesses import run_command


class ImmichUploadJobSummary(BaseModel):
    """A lean, read-only view of a clip's latest Immich-upload job.

    Deliberately not `jobs.models.JobSnapshot` — `jobs/models.py` already imports plan
    types from this module, so importing `JobSnapshot` back here would be circular.
    `jobs/repository.py` (which already depends on this module) converts a
    `JobSnapshot` into this shape when building the batched per-clip lookup.
    """

    id: str
    state: str
    stage: str
    progress: float
    message: str
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class ClipRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    custom_title: str | None = None
    library: str
    media_type: str
    duration_ms: int
    revision: int
    movie_title: str | None = None
    movie_year: int | None = None
    show_name: str | None = None
    episode_title: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    clip_number: int = 1
    plex_username: str | None = None
    source_start_ms: int
    source_end_ms: int
    created_at: datetime
    updated_at: datetime
    thumbnail_url: str
    play_url: str
    download_url: str
    immich_asset_id: str | None = None
    immich_upload_job: ImmichUploadJobSummary | None = None


class ClipPage(BaseModel):
    items: list[ClipRecord]
    page: int
    page_size: int
    total: int
    pages: int


class ClipEpisodeFilterOption(BaseModel):
    show_name: str
    title: str
    season_number: int | None = None
    episode_number: int | None = None


class ClipFilterOptions(BaseModel):
    libraries: list[str]
    movies: list[str]
    shows: list[str]
    episodes: list[ClipEpisodeFilterOption]


class ClipMetadataUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    custom_title: str | None = Field(default=None, max_length=160)
    library: str | None = Field(default=None, min_length=1, max_length=160)
    media_type: Literal["movie", "episode", "video"] | None = None
    movie_title: str | None = Field(default=None, max_length=240)
    movie_year: int | None = Field(default=None, ge=1800, le=3000)
    show_name: str | None = Field(default=None, max_length=240)
    episode_title: str | None = Field(default=None, max_length=240)
    season_number: int | None = Field(default=None, ge=0, le=999)
    episode_number: int | None = Field(default=None, ge=0, le=9999)

    @field_validator(
        "custom_title", "library", "movie_title", "show_name", "episode_title", mode="before"
    )
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = " ".join(value.split())
        return normalized or None


class ClipDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


class ClipDeleteResult(BaseModel):
    id: str
    title: str
    deleted: bool = True
    cleanup_warnings: list[str] = Field(default_factory=list)


class MetadataEditJobPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    job_id: str
    clip_id: str
    expected_revision: int
    proposed: dict[str, Any]
    destination: str
    operation_hash: str


class ThumbnailJobPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    job_id: str
    clip_id: str
    clip_revision: int
    source_size: int
    source_modified_ns: int
    operation_hash: str


class ImmichUploadJobPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    job_id: str
    clip_id: str
    # Also doubles as the dedup key for `_find_active_job` — only one upload job may
    # be in flight per clip at a time, mirroring the thumbnail job's hash-based dedup.
    operation_hash: str


def build_immich_upload_plan(clip: dict[str, Any]) -> ImmichUploadJobPlan:
    return ImmichUploadJobPlan(
        job_id=f"job-{uuid4()}",
        clip_id=str(clip["id"]),
        operation_hash=str(clip["id"]),
    )


class BulkImmichUploadJobPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    job_id: str
    clip_ids: list[str]
    # A fixed constant, not per-clip: only one bulk upload may be in flight at a
    # time (the dedup this enables is a courtesy, not a correctness guard — each
    # clip is re-checked against its current state before processing, so even a
    # redundant concurrent bulk job would just skip everything the first already
    # handled rather than double-upload anything).
    operation_hash: str


BULK_IMMICH_UPLOAD_OPERATION_HASH = "bulk_immich_upload"


def build_bulk_immich_upload_plan(clip_ids: list[str]) -> BulkImmichUploadJobPlan:
    return BulkImmichUploadJobPlan(
        job_id=f"job-{uuid4()}",
        clip_ids=clip_ids,
        operation_hash=BULK_IMMICH_UPLOAD_OPERATION_HASH,
    )


class ClipRevisionConflict(RuntimeError):
    job_error_code = "CLIP_REVISION_CONFLICT"
    job_retryable = False


class ClipAssetUnavailable(RuntimeError):
    job_error_code = "CLIP_ASSET_UNAVAILABLE"
    job_retryable = True


class ClipDeleteSafetyError(RuntimeError):
    """Raised before deletion when a stored asset is outside its managed root."""


async def delete_clip(
    engine: AsyncEngine,
    clip_id: str,
    expected_revision: int,
    *,
    clip_root: Path,
    thumbnail_root: Path,
    run_blocking: Callable[..., Awaitable[Any]],
) -> ClipDeleteResult | None:
    """Delete only validated managed assets, then remove the durable clip record."""
    async with engine.connect() as connection:
        await connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            row = (
                await connection.execute(
                    text("SELECT * FROM clips WHERE id = :id"), {"id": clip_id}
                )
            ).mappings().first()
            if row is None:
                await connection.rollback()
                return None
            clip = dict(row)
            if int(clip["revision"]) != expected_revision:
                raise ClipRevisionConflict(
                    f"Clip revision {expected_revision} is stale; current revision is "
                    f"{clip['revision']}."
                )
            warnings = await run_blocking(
                _delete_managed_clip_assets,
                clip,
                clip_root,
                thumbnail_root,
            )
            result = await connection.execute(
                text("DELETE FROM clips WHERE id = :id AND revision = :revision"),
                {"id": clip_id, "revision": expected_revision},
            )
            if result.rowcount != 1:
                raise ClipRevisionConflict("The clip changed before deletion completed.")
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
    return ClipDeleteResult(
        id=clip_id,
        title=str(clip["title"]),
        cleanup_warnings=warnings,
    )


def _delete_managed_clip_assets(
    clip: dict[str, Any], clip_root: Path, thumbnail_root: Path
) -> list[str]:
    """Validate every deletion target together, then remove the managed files."""
    media = _validated_delete_target(
        clip.get("file_path"), clip_root, "clip media", required=True
    )
    thumbnail = _validated_delete_target(
        clip.get("thumbnail_path"), thumbnail_root, "thumbnail", required=False
    )

    warnings: list[str] = []
    if media.exists():
        try:
            media.unlink()
        except OSError as error:
            raise ClipAssetUnavailable("The managed clip could not be deleted.") from error
    else:
        warnings.append("The managed clip file was already missing.")

    if thumbnail is not None and thumbnail.exists():
        try:
            thumbnail.unlink()
        except OSError:
            warnings.append("The thumbnail could not be removed.")
    return warnings


def _validated_delete_target(
    value: object,
    root: Path,
    label: str,
    *,
    required: bool,
) -> Path | None:
    if value is None:
        if required:
            raise ClipDeleteSafetyError(f"The stored {label} path is missing.")
        return None
    resolved_root = root.resolve(strict=False)
    stored = Path(str(value))
    lexical = Path(os.path.abspath(stored))
    try:
        relative = lexical.relative_to(resolved_root)
    except ValueError as error:
        raise ClipDeleteSafetyError(
            f"The stored {label} path is outside its managed root."
        ) from error
    candidate = resolved_root
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            raise ClipDeleteSafetyError(f"The stored {label} path contains a symbolic link.")
    resolved = lexical.resolve(strict=False)
    if not resolved.is_relative_to(resolved_root):
        raise ClipDeleteSafetyError(f"The stored {label} path escapes its managed root.")
    if resolved.exists() and not resolved.is_file():
        raise ClipDeleteSafetyError(f"The stored {label} path is not a file.")
    return resolved


SORT_SQL = {
    "newest": "created_at DESC, id DESC",
    "oldest": "created_at ASC, id ASC",
    "title_asc": "title COLLATE NOCASE ASC, id ASC",
    "title_desc": "title COLLATE NOCASE DESC, id DESC",
    "duration_asc": "duration_ms ASC, id ASC",
    "duration_desc": "duration_ms DESC, id DESC",
}


async def list_clips(
    engine: AsyncEngine,
    *,
    page: int = 1,
    page_size: int | None = 24,
    search: str | None = None,
    library: str | None = None,
    media_type: str | None = None,
    media: list[str] | None = None,
    episode: list[str] | None = None,
    sort: str = "newest",
) -> ClipPage:
    clauses: list[str] = []
    values: dict[str, Any] = {}
    if search:
        clauses.append(
            "(title LIKE :search ESCAPE '\\' OR library LIKE :search ESCAPE '\\' "
            "OR COALESCE(movie_title, '') LIKE :search ESCAPE '\\' "
            "OR COALESCE(show_name, '') LIKE :search ESCAPE '\\' "
            "OR COALESCE(episode_title, '') LIKE :search ESCAPE '\\')"
        )
        values["search"] = f"%{_escape_like(search)}%"
    if library:
        clauses.append("library = :library COLLATE NOCASE")
        values["library"] = library
    if media_type:
        clauses.append("media_type = :media_type")
        values["media_type"] = media_type
    if media:
        media_values = [value for value in dict.fromkeys(media) if value]
        if media_values:
            placeholders = []
            for index, value in enumerate(media_values):
                key = f"media_{index}"
                placeholders.append(f":{key}")
                values[key] = value
            clauses.append(
                "COALESCE(show_name, movie_title, title) IN "
                f"({', '.join(placeholders)})"
            )
    if episode:
        episode_values = [value for value in dict.fromkeys(episode) if value]
        if episode_values:
            placeholders = []
            for index, value in enumerate(episode_values):
                key = f"episode_{index}"
                placeholders.append(f":{key}")
                values[key] = value
            clauses.append(f"episode_title IN ({', '.join(placeholders)})")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    order = SORT_SQL.get(sort, SORT_SQL["newest"])
    async with engine.connect() as connection:
        total = int(
            await connection.scalar(text(f"SELECT COUNT(*) FROM clips{where}"), values) or 0
        )
        if page_size is None:
            effective_page_size = max(total, 1)
            pagination = ""
            effective_page = 1
        else:
            effective_page_size = page_size
            pagination = " LIMIT :limit OFFSET :offset"
            effective_page = page
            values["limit"] = page_size
            values["offset"] = (page - 1) * page_size
        rows = (
            await connection.execute(
                text(f"SELECT * FROM clips{where} ORDER BY {order}{pagination}"),
                values,
            )
        ).mappings().all()
    return ClipPage(
        items=[public_clip(dict(row)) for row in rows],
        page=effective_page,
        page_size=effective_page_size,
        total=total,
        pages=max(1, (total + effective_page_size - 1) // effective_page_size),
    )


async def list_unlinked_clip_ids(engine: AsyncEngine, normalized_immich_url: str) -> list[str]:
    """Clip ids not linked to *this* Immich server — no association at all, or one
    recorded against a different, previously-configured server (which is not
    linked to the one we'd actually be uploading to)."""
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT id FROM clips "
                    "WHERE immich_asset_id IS NULL OR immich_server_url IS NOT :url "
                    "ORDER BY created_at"
                ),
                {"url": normalized_immich_url},
            )
        ).all()
    return [str(row[0]) for row in rows]


async def list_libraries(engine: AsyncEngine) -> list[str]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text("SELECT DISTINCT library FROM clips ORDER BY library COLLATE NOCASE")
            )
        ).all()
    return [str(row[0]) for row in rows]


async def list_filter_options(
    engine: AsyncEngine, plex_libraries: list[str] | None = None
) -> ClipFilterOptions:
    async with engine.connect() as connection:
        library_rows = (
            await connection.execute(
                text("SELECT DISTINCT library FROM clips ORDER BY library COLLATE NOCASE")
            )
        ).all()
        movie_rows = (
            await connection.execute(
                text(
                    "SELECT DISTINCT movie_title FROM clips "
                    "WHERE movie_title IS NOT NULL AND trim(movie_title) <> '' "
                    "ORDER BY movie_title COLLATE NOCASE"
                )
            )
        ).all()
        show_rows = (
            await connection.execute(
                text(
                    "SELECT DISTINCT show_name FROM clips "
                    "WHERE show_name IS NOT NULL AND trim(show_name) <> '' "
                    "ORDER BY show_name COLLATE NOCASE"
                )
            )
        ).all()
        episode_rows = (
            await connection.execute(
                text(
                    "SELECT DISTINCT show_name, episode_title, season_number, episode_number "
                    "FROM clips "
                    "WHERE show_name IS NOT NULL AND trim(show_name) <> '' "
                    "AND episode_title IS NOT NULL AND trim(episode_title) <> '' "
                    "ORDER BY show_name COLLATE NOCASE, season_number, episode_number, "
                    "episode_title COLLATE NOCASE"
                )
            )
        ).all()
    preferred = {name.casefold(): name for name in plex_libraries or []}
    libraries_by_key: dict[str, str] = {}
    for row in library_rows:
        stored = str(row[0])
        key = stored.casefold()
        libraries_by_key.setdefault(key, preferred.get(key, stored))
    return ClipFilterOptions(
        libraries=sorted(libraries_by_key.values(), key=str.casefold),
        movies=_case_insensitive_values(movie_rows),
        shows=_case_insensitive_values(show_rows),
        episodes=[
            ClipEpisodeFilterOption(
                show_name=str(row[0]),
                title=str(row[1]),
                season_number=int(row[2]) if row[2] is not None else None,
                episode_number=int(row[3]) if row[3] is not None else None,
            )
            for row in episode_rows
        ],
    )


def _case_insensitive_values(rows: list[Any]) -> list[str]:
    values: dict[str, str] = {}
    for row in rows:
        display = str(row[0])
        values.setdefault(display.casefold(), display)
    return sorted(values.values(), key=str.casefold)


def public_clip(
    row: dict[str, Any], *, immich_upload_job: ImmichUploadJobSummary | None = None
) -> ClipRecord:
    clip_id = str(row["id"])
    return ClipRecord.model_validate(
        {
            **row,
            "thumbnail_url": f"/api/clips/{clip_id}/thumbnail",
            "play_url": f"/api/clips/{clip_id}/media",
            "download_url": f"/api/clips/{clip_id}/download",
            "immich_upload_job": immich_upload_job,
        }
    )


def build_metadata_edit_plan(
    row: dict[str, Any], update: ClipMetadataUpdate, clip_root: Path
) -> MetadataEditJobPlan:
    if int(row["revision"]) != update.expected_revision:
        raise ClipRevisionConflict(
            f"Clip revision {update.expected_revision} is stale; current revision is "
            f"{row['revision']}."
        )
    editable = {
        "custom_title",
        "library",
        "media_type",
        "movie_title",
        "movie_year",
        "show_name",
        "episode_title",
        "season_number",
        "episode_number",
    }
    proposed = dict(row)
    for field in editable & update.model_fields_set:
        proposed[field] = getattr(update, field)
    proposed["library"] = sanitize_display_name(str(proposed["library"]))
    proposed["custom_title"] = _optional_sanitized(proposed.get("custom_title"))
    proposed["automatic_title"] = automatic_title(proposed)
    proposed["title"] = display_title(proposed)
    proposed["revision"] = int(row["revision"]) + 1
    destination = resolve_unique_clip_path(
        clip_root,
        str(proposed["library"]),
        str(proposed["title"]),
        exclude=Path(str(row["file_path"])),
    )
    payload = {
        key: proposed.get(key)
        for key in (
            "id",
            "title",
            "custom_title",
            "automatic_title",
            "library",
            "media_type",
            "movie_title",
            "movie_year",
            "show_name",
            "episode_title",
            "season_number",
            "episode_number",
            "clip_number",
            "plex_username",
            "duration_ms",
            "revision",
            "source_start_ms",
            "source_end_ms",
            "source_path",
            "source_size_bytes",
            "source_modified_at",
            "selected_audio_stream_index",
            "render_plan_hash",
            "created_at",
        )
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    operation_hash = hashlib.sha256(canonical.encode()).hexdigest()
    return MetadataEditJobPlan(
        job_id=f"job-{uuid4()}",
        clip_id=str(row["id"]),
        expected_revision=update.expected_revision,
        proposed=payload,
        destination=str(destination),
        operation_hash=operation_hash,
    )


def build_thumbnail_job_plan(row: dict[str, Any], source_stat: os.stat_result) -> ThumbnailJobPlan:
    payload = (
        f"{row['id']}:{row['revision']}:{source_stat.st_size}:{source_stat.st_mtime_ns}"
    )
    return ThumbnailJobPlan(
        job_id=f"job-{uuid4()}",
        clip_id=str(row["id"]),
        clip_revision=int(row["revision"]),
        source_size=source_stat.st_size,
        source_modified_ns=source_stat.st_mtime_ns,
        operation_hash=hashlib.sha256(payload.encode()).hexdigest(),
    )


def display_title(metadata: dict[str, Any]) -> str:
    custom = _optional_sanitized(metadata.get("custom_title"))
    if custom:
        return custom
    return automatic_title(metadata)


def automatic_title(metadata: dict[str, Any]) -> str:
    if metadata.get("media_type") == "episode":
        show = _clean(metadata.get("show_name"))
        episode_title = _clean(metadata.get("episode_title"))
        season = metadata.get("season_number")
        episode = metadata.get("episode_number")
        code = (
            f"S{int(season):02d}E{int(episode):02d}"
            if season is not None and episode is not None
            else None
        )
        parts = [part for part in (show, code, episode_title) if part]
        if parts:
            return sanitize_display_name(" - ".join(parts))
    if metadata.get("media_type") == "movie":
        movie = _clean(metadata.get("movie_title"))
        year = metadata.get("movie_year")
        if movie:
            return sanitize_display_name(f"{movie} ({year})" if year else movie)
    return sanitize_display_name(
        str(metadata.get("automatic_title") or metadata.get("title") or "Untitled Clip")
    )


def _sanitize_tag_segment(value: str) -> str:
    """A tag-path segment must not itself contain "/" — that's the hierarchy
    separator Immich's tag upsert parses on — so a literal slash in clip metadata
    (a show or episode title, say) is replaced rather than left to silently split
    into extra, unintended tag levels."""
    return value.replace("/", "-")


def build_immich_tag_paths(
    clip: dict[str, Any],
    *,
    default_tag: str,
    tag_library: bool,
    tag_show: bool,
    tag_episode: bool,
) -> list[str]:
    """Build the Immich tag paths to upsert and apply for a clip: the flat
    default tag (if configured) and a Library / Show(-or-Movie) / Episode
    hierarchy path (if any level is enabled and has data).

    Library and Show are independent of each other (neither gates the other)
    but both nest as path prefixes when present. Episode only ever nests under
    an actually-present Show segment — it depends on Show, not on Library, and
    never applies to a movie regardless of its own toggle.
    """
    paths: list[str] = []
    cleaned_default = _clean(default_tag)
    if cleaned_default:
        paths.append(_sanitize_tag_segment(cleaned_default))

    segments: list[str] = []
    if tag_library:
        library = _clean(clip.get("library"))
        if library:
            segments.append(_sanitize_tag_segment(library))

    show_appended = False
    if tag_show:
        # Select strictly by the clip's current media_type, not by whichever
        # field happens to be non-empty — a clip reclassified from episode to
        # movie (or vice versa) can still carry a stale show_name/movie_title
        # from before the change, and that must not leak into the tag path.
        media_type = clip.get("media_type")
        level_two = None
        if media_type == "episode":
            level_two = _clean(clip.get("show_name"))
        elif media_type == "movie":
            level_two = _clean(clip.get("movie_title"))
        if level_two:
            segments.append(_sanitize_tag_segment(level_two))
            show_appended = True

    if tag_episode and show_appended and clip.get("media_type") == "episode":
        season = clip.get("season_number")
        episode = clip.get("episode_number")
        episode_title = _clean(clip.get("episode_title"))
        code = (
            f"S{int(season):02d}E{int(episode):02d}"
            if season is not None and episode is not None
            else None
        )
        level_three = " - ".join(part for part in (code, episode_title) if part)
        if level_three:
            segments.append(_sanitize_tag_segment(level_three))

    if segments:
        paths.append("/".join(segments))
    return paths


def recovery_envelope(metadata: dict[str, Any]) -> str:
    payload = {
        "schemaVersion": 4,
        "application": "MediaClipMakarr",
        "clipId": metadata["id"],
        "revision": metadata["revision"],
        "createdAt": str(metadata["created_at"]),
        "updatedAt": str(metadata["updated_at"]),
        "metadata": {
            key: metadata.get(key)
            for key in (
                "title",
                "custom_title",
                "automatic_title",
                "library",
                "media_type",
                "movie_title",
                "movie_year",
                "show_name",
                "episode_title",
                "season_number",
                "episode_number",
                "clip_number",
                "plex_username",
                "immich_asset_id",
                "immich_server_url",
            )
        },
        "source": {
            "path": metadata.get("source_path"),
            "startMs": metadata.get("source_start_ms"),
            "endMs": metadata.get("source_end_ms"),
            "sizeBytes": metadata.get("source_size_bytes"),
            "modifiedAt": str(metadata.get("source_modified_at")),
            "selectedAudioStreamIndex": metadata.get("selected_audio_stream_index"),
        },
        "renderPlanHash": metadata.get("render_plan_hash"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    payload["checksum"] = hashlib.sha256(encoded.encode()).hexdigest()
    return "MediaClipMakarr " + json.dumps(payload, sort_keys=True, separators=(",", ":"))


async def rewrite_clip_metadata(
    source: Path,
    output: Path,
    metadata: dict[str, Any],
    *,
    ffmpeg_path: Path,
    timeout_seconds: float,
) -> None:
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await run_command(
        [
            ffmpeg_path,
            "-hide_banner",
            "-y",
            "-i",
            source,
            "-map",
            "0",
            "-c",
            "copy",
            "-map_metadata",
            "0",
            *_conventional_metadata_args(metadata),
            "-movflags",
            "+faststart",
            output,
        ],
        timeout_seconds=timeout_seconds,
    )


def _conventional_metadata_args(metadata: dict[str, Any]) -> list[str]:
    values = {
        "title": metadata.get("title"),
        "description": metadata.get("title"),
        "show": metadata.get("show_name"),
        "season_number": metadata.get("season_number"),
        "episode_sort": metadata.get("episode_number"),
        "date": metadata.get("movie_year"),
        "comment": recovery_envelope(metadata),
    }
    return [
        item
        for key, value in values.items()
        if value is not None
        for item in ("-metadata", f"{key}={value}")
    ]


async def generate_thumbnail(
    source: Path,
    output: Path,
    *,
    duration_ms: int,
    ffmpeg_path: Path,
    timeout_seconds: float,
) -> None:
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    seek_seconds = max(0.0, min(duration_ms / 3000, 10.0))
    await run_command(
        [
            ffmpeg_path,
            "-hide_banner",
            "-y",
            "-ss",
            f"{seek_seconds:.3f}",
            "-i",
            source,
            "-frames:v",
            "1",
            "-vf",
            "scale=640:-2:force_original_aspect_ratio=decrease",
            "-q:v",
            "3",
            output,
        ],
        timeout_seconds=timeout_seconds,
    )


def thumbnail_path(thumbnail_root: Path, clip_id: str) -> Path:
    safe_id = "".join(
        character for character in clip_id if character.isalnum() or character in "-_"
    )
    if not safe_id or safe_id != clip_id:
        raise ValueError("Invalid clip identity for thumbnail storage.")
    result = (thumbnail_root / f"{safe_id}.jpg").resolve(strict=False)
    if not result.is_relative_to(thumbnail_root.resolve(strict=False)):
        raise ValueError("Thumbnail path escaped the configured directory.")
    return result


def thumbnail_is_current(row: dict[str, Any], source_stat: os.stat_result) -> bool:
    path_value = row.get("thumbnail_path")
    return bool(
        path_value
        and Path(str(path_value)).is_file()
        and row.get("thumbnail_source_size") == source_stat.st_size
        and row.get("thumbnail_source_modified_ns") == source_stat.st_mtime_ns
    )


def embedded_revision_matches(path: Path, clip_id: str, revision: int) -> bool:
    """Inspect bounded MP4 regions for the current recovery envelope."""
    marker = b"MediaClipMakarr "
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            chunks = [handle.read(min(size, 4 * 1024 * 1024))]
            if size > 4 * 1024 * 1024:
                handle.seek(max(0, size - 4 * 1024 * 1024))
                chunks.append(handle.read(4 * 1024 * 1024))
    except OSError:
        return False
    for chunk in chunks:
        start = chunk.find(marker)
        while start >= 0:
            payload_start = start + len(marker)
            decoder = json.JSONDecoder()
            try:
                payload, _ = decoder.raw_decode(
                    chunk[payload_start:].decode("utf-8", errors="ignore")
                )
            except (json.JSONDecodeError, UnicodeDecodeError):
                start = chunk.find(marker, payload_start)
                continue
            if payload.get("clipId") == clip_id and payload.get("revision") == revision:
                return True
            start = chunk.find(marker, payload_start)
    return False


def _clean(value: object) -> str | None:
    return " ".join(str(value).split()) if value is not None and str(value).strip() else None


def _optional_sanitized(value: object) -> str | None:
    cleaned = _clean(value)
    return sanitize_display_name(cleaned) if cleaned else None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
