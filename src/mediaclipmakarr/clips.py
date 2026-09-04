from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mediaclipmakarr.plex import PlexSession, PlexSessionSnapshot
from mediaclipmakarr.source_media import ResolvedSourceMedia, SubtitleSelection

StructuredErrorCode = Literal[
    "CLIP_RANGE_NEGATIVE",
    "CLIP_RANGE_ORDER",
    "CLIP_RANGE_DURATION_EXCEEDED",
    "PLEX_SESSION_NOT_FOUND",
    "PLEX_MEDIA_CHANGED",
    "PLEX_SESSIONS_UNAVAILABLE",
    "PLEX_SOURCE_PART_UNAVAILABLE",
    "SOURCE_PATH_UNMAPPED",
    "SOURCE_PATH_REJECTED",
    "SOURCE_PATH_MISSING",
    "SOURCE_PROBE_UNAVAILABLE",
    "SOURCE_PROBE_FAILED",
    "SOURCE_PROBE_INVALID",
    "VIDEO_STREAM_UNAVAILABLE",
    "AUDIO_STREAM_UNAVAILABLE",
    "AUDIO_STREAM_AMBIGUOUS",
    "SUBTITLE_STREAM_UNAVAILABLE",
    "SUBTITLE_STREAM_AMBIGUOUS",
    "SUBTITLE_STREAM_UNSUPPORTED",
    "DOLBY_VISION_PROFILE_5_UNSUPPORTED",
    "DOLBY_VISION_BASE_LAYER_INDETERMINATE",
]


class StructuredError(BaseModel):
    code: str
    message: str
    retryable: bool = False
    alternatives: list[dict[str, object]] | None = None


class ClipCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_identity: str = Field(min_length=1)
    media_identity: str = Field(min_length=1)
    start_ms: int
    end_ms: int
    audio_stream_index: int | None = None
    subtitle_stream_index: int | None = None
    subtitles_enabled: bool = False


class ClipCreateValidationResult(BaseModel):
    valid: bool
    code: str
    message: str
    session_identity: str
    media_identity: str
    start_ms: int
    end_ms: int
    duration_ms: int
    validated_at: datetime
    source_media: ResolvedSourceMedia | None = None
    subtitle_selection: SubtitleSelection | None = None


class ClipCreateValidationError(Exception):
    def __init__(
        self,
        code: StructuredErrorCode,
        message: str,
        *,
        status_code: int = 422,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error = StructuredError(code=code, message=message, retryable=retryable)


async def get_clip(
    engine: AsyncEngine, clip_id: str, clip_root: Path
) -> dict[str, object] | None:
    """Load a managed clip only when its stored path remains under the clip root."""
    async with engine.connect() as connection:
        row = (
            await connection.execute(text("SELECT * FROM clips WHERE id = :id"), {"id": clip_id})
        ).mappings().first()
    if row is None:
        return None
    clip = dict(row)
    path, resolved_root = await asyncio.gather(
        asyncio.to_thread(Path(str(clip["file_path"])).resolve, strict=False),
        asyncio.to_thread(clip_root.resolve, strict=False),
    )
    if not path.is_relative_to(resolved_root) or not await asyncio.to_thread(path.is_file):
        return None
    clip["file_path"] = str(path)
    return clip


class ImmichAssetAssociationConflict(RuntimeError):
    """Raised when a clip already has a different Immich asset recorded for this server."""

    job_error_code = "IMMICH_ASSET_ASSOCIATION_FAILED"
    job_retryable = False


async def set_clip_immich_asset_id(
    engine: AsyncEngine, clip_id: str, asset_id: str, server_url: str
) -> None:
    """Durably record a new Immich asset association for a clip.

    Refuses to silently overwrite an association already recorded for the *same*
    server (a genuine conflict, e.g. a concurrent duplicate run landing between this
    caller's read and this write) while still allowing replacement when the
    configured server has changed. Also clears any cached `immich_tag_ids` in the
    same write: that cache belongs to whichever asset+server was previously
    recorded, and leaving it in place would let a later run reload tag ids that are
    foreign to the new association and send them to the new server.
    """
    async with engine.begin() as connection:
        result = await connection.execute(
            text(
                "UPDATE clips SET immich_asset_id = :asset_id, immich_server_url = :server_url, "
                "immich_tag_ids = NULL "
                "WHERE id = :id AND "
                "(immich_asset_id IS NULL OR immich_server_url IS NOT :server_url)"
            ),
            {"id": clip_id, "asset_id": asset_id, "server_url": server_url},
        )
        if result.rowcount != 1:
            raise ImmichAssetAssociationConflict(
                f"Clip {clip_id} already has a different Immich asset recorded for this server."
            )


async def clear_clip_immich_asset_id(
    engine: AsyncEngine, clip_id: str, *, expected_asset_id: str
) -> bool:
    """Drop a clip's Immich association after its asset was confirmed gone
    (deleted directly in Immich, detected via a 404 on read/update).

    Without this, `set_clip_immich_asset_id`'s same-server guard would refuse
    to record the id from a fresh re-upload — it only ever replaces an
    association for a *different* server, not a stale one for the same server.

    Only clears when the clip's *currently stored* `immich_asset_id` still
    matches `expected_asset_id` — the same compare-and-swap guard
    `set_clip_immich_asset_id` already uses on the write side, applied here to
    the clear side. Without it, a concurrent run (e.g. the bulk job processing
    the same clip against an earlier snapshot, or two retry paths racing) could
    have already replaced the association with a newer, valid one between the
    caller's read and this clear — an unconditional clear would silently
    orphan that newer asset. Returns whether anything was actually cleared;
    callers must not proceed with a fresh upload when this returns `False`,
    since that means the stored association has already moved on and a fresh
    upload would create a needless duplicate.
    """
    async with engine.begin() as connection:
        result = await connection.execute(
            text(
                "UPDATE clips SET immich_asset_id = NULL, immich_server_url = NULL, "
                "immich_tag_ids = NULL WHERE id = :id AND immich_asset_id = :expected_asset_id"
            ),
            {"id": clip_id, "expected_asset_id": expected_asset_id},
        )
        return result.rowcount == 1


def parse_stored_immich_tag_ids(value: object) -> list[str]:
    """Decode the `immich_tag_ids` column (a JSON array, or NULL) into a list."""
    if not value:
        return []
    try:
        decoded = json.loads(str(value))
    except ValueError:
        return []
    return [str(item) for item in decoded] if isinstance(decoded, list) else []


async def set_clip_immich_tag_ids(engine: AsyncEngine, clip_id: str, tag_ids: list[str]) -> None:
    """Durably record which Immich tag ids are currently applied to a clip.

    This is a best-effort cache of reality, not a guarded association like
    `set_clip_immich_asset_id` — it exists purely so the next upload/organize run
    can diff against it to remove tags that are no longer wanted (e.g. after the
    clip's library or show name changes), rather than only ever adding new ones.
    """
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE clips SET immich_tag_ids = :tag_ids WHERE id = :id"),
            {"id": clip_id, "tag_ids": json.dumps(tag_ids)},
        )


async def insert_clip(engine: AsyncEngine, clip: dict[str, object]) -> None:
    """Persist a newly installed managed clip."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO clips "
                "(id, title, library, media_type, file_path, duration_ms, revision, "
                "source_start_ms, source_end_ms, source_path, source_size_bytes, "
                "source_modified_at, selected_audio_stream_index, render_plan_hash, "
                "created_at, updated_at, custom_title, automatic_title, movie_title, "
                "movie_year, show_name, "
                "episode_title, season_number, episode_number, clip_number, plex_username, "
                "file_size_bytes, file_modified_ns) "
                "VALUES (:id, :title, :library, :media_type, :file_path, :duration_ms, "
                ":revision, :source_start_ms, :source_end_ms, :source_path, "
                ":source_size_bytes, :source_modified_at, :selected_audio_stream_index, "
                ":render_plan_hash, :created_at, :updated_at, :custom_title, "
                ":automatic_title, :movie_title, "
                ":movie_year, :show_name, :episode_title, :season_number, :episode_number, "
                ":clip_number, :plex_username, :file_size_bytes, :file_modified_ns)"
            ),
            _insert_values(clip),
        )


async def insert_clip_if_missing(engine: AsyncEngine, clip: dict[str, object]) -> None:
    """Persist recovery output without replacing an existing clip identity."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT OR IGNORE INTO clips "
                "(id, title, library, media_type, file_path, duration_ms, revision, "
                "source_start_ms, source_end_ms, source_path, source_size_bytes, "
                "source_modified_at, selected_audio_stream_index, render_plan_hash, "
                "created_at, updated_at, custom_title, automatic_title, movie_title, "
                "movie_year, show_name, "
                "episode_title, season_number, episode_number, clip_number, plex_username, "
                "file_size_bytes, file_modified_ns) "
                "VALUES (:id, :title, :library, :media_type, :file_path, :duration_ms, "
                ":revision, :source_start_ms, :source_end_ms, :source_path, "
                ":source_size_bytes, :source_modified_at, :selected_audio_stream_index, "
                ":render_plan_hash, :created_at, :updated_at, :custom_title, "
                ":automatic_title, :movie_title, "
                ":movie_year, :show_name, :episode_title, :season_number, :episode_number, "
                ":clip_number, :plex_username, :file_size_bytes, :file_modified_ns)"
            ),
            _insert_values(clip),
        )


def _insert_values(clip: dict[str, object]) -> dict[str, object]:
    return {
        "custom_title": None,
        "automatic_title": clip.get("title"),
        "movie_title": None,
        "movie_year": None,
        "show_name": None,
        "episode_title": None,
        "season_number": None,
        "episode_number": None,
        "clip_number": 1,
        "plex_username": None,
        "file_size_bytes": None,
        "file_modified_ns": None,
        **clip,
    }


def validate_clip_create_request(
    request: ClipCreateRequest, snapshot: PlexSessionSnapshot
) -> ClipCreateValidationResult:
    if request.start_ms < 0 or request.end_ms < 0:
        raise ClipCreateValidationError(
            "CLIP_RANGE_NEGATIVE",
            "Clip boundaries must be zero or later.",
        )
    if request.end_ms <= request.start_ms:
        raise ClipCreateValidationError(
            "CLIP_RANGE_ORDER",
            "End must be later than Start.",
        )

    if snapshot.status != "ok":
        raise ClipCreateValidationError(
            "PLEX_SESSIONS_UNAVAILABLE",
            snapshot.message,
            status_code=409,
            retryable=True,
        )

    session = _find_session(snapshot.sessions, request.session_identity)
    if session is None:
        raise ClipCreateValidationError(
            "PLEX_SESSION_NOT_FOUND",
            "The selected Plex session is no longer active.",
            status_code=409,
            retryable=True,
        )
    if session.media_identity != request.media_identity:
        raise ClipCreateValidationError(
            "PLEX_MEDIA_CHANGED",
            "The selected Plex player changed media. Capture the clip boundaries again.",
            status_code=409,
            retryable=False,
        )
    if session.duration_ms is not None and request.end_ms > session.duration_ms:
        raise ClipCreateValidationError(
            "CLIP_RANGE_DURATION_EXCEEDED",
            "End must be within the selected media duration.",
        )

    return ClipCreateValidationResult(
        valid=True,
        code="CLIP_REQUEST_VALIDATED",
        message=(
            "Clip request validated. Durable clip job creation will be added with the job runner."
        ),
        session_identity=session.session_identity,
        media_identity=session.media_identity,
        start_ms=request.start_ms,
        end_ms=request.end_ms,
        duration_ms=request.end_ms - request.start_ms,
        validated_at=snapshot.sampled_at,
    )


def _find_session(
    sessions: list[PlexSession], session_identity: str
) -> PlexSession | None:
    return next(
        (
            session
            for session in sessions
            if session.session_identity == session_identity
        ),
        None,
    )
