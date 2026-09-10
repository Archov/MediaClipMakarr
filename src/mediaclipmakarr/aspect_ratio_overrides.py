from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


@dataclass(frozen=True)
class AspectRatioIdentity:
    """Everything known about a piece of content that could key a persisted
    aspect-ratio override, tried in priority order on lookup: external ids
    (IMDb/TMDb/TVDb — the most portable, survive a Plex rescan or migration),
    then the show/movie's folder path (survives a Plex rescan but not a file
    move/rename), then Plex's own internal rating key (survives a file move
    but not a rescan that re-matches the item)."""

    external_ids: list[str] = field(default_factory=list)
    path_key: str | None = None
    plex_rating_key: str | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def lookup_aspect_ratio_override(
    engine: AsyncEngine, identity: AspectRatioIdentity
) -> str | None:
    async with engine.connect() as connection:
        override_id = await _find_override_id(connection, identity)
        if override_id is None:
            return None
        return await connection.scalar(
            text("SELECT aspect_ratio FROM aspect_ratio_overrides WHERE id = :id"),
            {"id": override_id},
        )


async def set_aspect_ratio_override(
    engine: AsyncEngine, identity: AspectRatioIdentity, aspect_ratio: str
) -> None:
    """Persist `aspect_ratio` for this show/movie. Every identity fact known
    at write time (external ids, path key, rating key) is stored on the same
    record — even ones not used to *find* it this time — so a later lookup can
    still hit via whichever of those facts is still valid."""
    now = _utc_now()
    async with engine.begin() as connection:
        override_id = await _find_override_id(connection, identity)
        if override_id is None:
            override_id = f"aro-{uuid4()}"
            await connection.execute(
                text(
                    "INSERT INTO aspect_ratio_overrides "
                    "(id, path_key, plex_rating_key, aspect_ratio, created_at, updated_at) "
                    "VALUES (:id, :path_key, :plex_rating_key, :aspect_ratio, :now, :now)"
                ),
                {
                    "id": override_id,
                    "path_key": identity.path_key,
                    "plex_rating_key": identity.plex_rating_key,
                    "aspect_ratio": aspect_ratio,
                    "now": now,
                },
            )
        else:
            await connection.execute(
                text(
                    "UPDATE aspect_ratio_overrides SET "
                    "aspect_ratio = :aspect_ratio, "
                    "path_key = COALESCE(:path_key, path_key), "
                    "plex_rating_key = COALESCE(:plex_rating_key, plex_rating_key), "
                    "updated_at = :now "
                    "WHERE id = :id"
                ),
                {
                    "id": override_id,
                    "path_key": identity.path_key,
                    "plex_rating_key": identity.plex_rating_key,
                    "aspect_ratio": aspect_ratio,
                    "now": now,
                },
            )
        for external_id in identity.external_ids:
            await connection.execute(
                text(
                    "INSERT OR IGNORE INTO aspect_ratio_override_external_ids "
                    "(override_id, external_id) VALUES (:override_id, :external_id)"
                ),
                {"override_id": override_id, "external_id": external_id},
            )


async def clear_aspect_ratio_override(
    engine: AsyncEngine, identity: AspectRatioIdentity
) -> bool:
    """Remove a persisted override (e.g. the user switched a show/movie back
    to Auto). Returns whether one existed."""
    async with engine.begin() as connection:
        override_id = await _find_override_id(connection, identity)
        if override_id is None:
            return False
        await connection.execute(
            text("DELETE FROM aspect_ratio_overrides WHERE id = :id"), {"id": override_id}
        )
        return True


async def _find_override_id(
    connection: AsyncConnection, identity: AspectRatioIdentity
) -> str | None:
    if identity.external_ids:
        stmt = text(
            "SELECT o.id FROM aspect_ratio_overrides o "
            "JOIN aspect_ratio_override_external_ids e ON e.override_id = o.id "
            "WHERE e.external_id IN :external_ids LIMIT 1"
        ).bindparams(bindparam("external_ids", expanding=True))
        override_id = await connection.scalar(stmt, {"external_ids": identity.external_ids})
        if override_id is not None:
            return str(override_id)
    if identity.path_key:
        override_id = await connection.scalar(
            text("SELECT id FROM aspect_ratio_overrides WHERE path_key = :path_key LIMIT 1"),
            {"path_key": identity.path_key},
        )
        if override_id is not None:
            return str(override_id)
    if identity.plex_rating_key:
        override_id = await connection.scalar(
            text(
                "SELECT id FROM aspect_ratio_overrides "
                "WHERE plex_rating_key = :plex_rating_key LIMIT 1"
            ),
            {"plex_rating_key": identity.plex_rating_key},
        )
        if override_id is not None:
            return str(override_id)
    return None
