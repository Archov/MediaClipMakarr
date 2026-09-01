from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mediaclipmakarr.clips import ClipCreateRequest
from mediaclipmakarr.hdr import HdrCapabilities, HdrRenderStrategy, planned_hdr_strategy
from mediaclipmakarr.plex import PlexSession
from mediaclipmakarr.source_media import (
    MediaStreamIdentity,
    ResolvedSourceMedia,
    SubtitleSelection,
)
from mediaclipmakarr.source_metadata import infer_source_organizing_metadata

OutputProfileId = Literal["p1-h264-aac-sdr-v1", "p2-h264-aac-sdr-v1"]

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class ClipRenderPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 2
    job_id: str
    clip_id: str
    revision: int = 1
    title: str
    library: str
    media_type: str
    custom_title: str | None = None
    automatic_title: str | None = None
    movie_title: str | None = None
    movie_year: int | None = None
    show_name: str | None = None
    episode_title: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    clip_number: int = 1
    session_identity: str
    media_identity: str
    plex_rating_key: str | None = None
    plex_media_key: str | None = None
    plex_part_id: str | None = None
    plex_part_key: str | None = None
    plex_user: str | None = None
    source_media: ResolvedSourceMedia
    source_start_ms: int
    source_end_ms: int
    selected_audio_stream: MediaStreamIdentity
    selected_subtitle: SubtitleSelection = Field(default_factory=SubtitleSelection)
    hdr: HdrCapabilities = Field(default_factory=HdrCapabilities)
    hdr_strategy: HdrRenderStrategy = "sdr"
    output_profile: OutputProfileId = "p2-h264-aac-sdr-v1"
    x264_preset: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    render_plan_hash: str

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_subtitle_stream_index(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = value.copy()
        normalized.pop("subtitle_stream_index", None)
        normalized.pop("selected_subtitle_stream_index", None)
        return normalized


def build_clip_render_plan(
    *,
    session: PlexSession,
    request: ClipCreateRequest,
    source_media: ResolvedSourceMedia,
    x264_preset: str,
) -> ClipRenderPlan:
    hdr = (
        source_media.capabilities.hdr
        if source_media.capabilities is not None
        else HdrCapabilities()
    )
    inferred = infer_source_organizing_metadata(
        source_media.local_path, session.media_type
    )
    movie_title = session.movie_title or inferred.movie_title
    movie_year = session.movie_year or inferred.movie_year
    show_name = session.show_name or inferred.show_name
    episode_title = session.episode_title or inferred.episode_title
    season_number = (
        session.season_number
        if session.season_number is not None
        else inferred.season_number
    )
    episode_number = (
        session.episode_number
        if session.episode_number is not None
        else inferred.episode_number
    )
    title = automatic_title(
        session,
        movie_title=movie_title,
        movie_year=movie_year,
        show_name=show_name,
        episode_title=episode_title,
        season_number=season_number,
        episode_number=episode_number,
    )
    payload: dict[str, Any] = {
        "job_id": f"job-{uuid4()}",
        "clip_id": f"clip-{uuid4()}",
        "title": title,
        "automatic_title": title,
        "library": sanitize_display_name(
            session.library
            or inferred.library
            or default_library_for_media_type(session.media_type)
        ),
        "media_type": session.media_type,
        "movie_title": movie_title,
        "movie_year": movie_year,
        "show_name": show_name,
        "episode_title": episode_title,
        "season_number": season_number,
        "episode_number": episode_number,
        "session_identity": session.session_identity,
        "media_identity": session.media_identity,
        "plex_rating_key": session.plex_rating_key,
        "plex_media_key": session.plex_media_key,
        "plex_part_id": session.plex_part_id,
        "plex_part_key": session.plex_part_key,
        "plex_user": session.plex_user,
        "source_media": source_media,
        "source_start_ms": request.start_ms,
        "source_end_ms": request.end_ms,
        "selected_audio_stream": source_media.selected_audio_stream,
        "selected_subtitle": source_media.selected_subtitle,
        "hdr": hdr,
        "hdr_strategy": planned_hdr_strategy(hdr),
        "x264_preset": x264_preset,
        "render_plan_hash": "",
    }
    plan = ClipRenderPlan.model_validate(payload)
    plan.render_plan_hash = render_plan_hash(plan)
    return plan


def render_plan_hash(plan: ClipRenderPlan) -> str:
    payload = plan.model_dump(mode="json")
    payload["render_plan_hash"] = ""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def default_library_for_media_type(media_type: str) -> str:
    return "TV Shows" if media_type == "episode" else "Movies"


def automatic_title(
    session: PlexSession,
    *,
    movie_title: str | None = None,
    movie_year: int | None = None,
    show_name: str | None = None,
    episode_title: str | None = None,
    season_number: int | None = None,
    episode_number: int | None = None,
) -> str:
    movie_title = movie_title or session.movie_title
    movie_year = movie_year if movie_year is not None else session.movie_year
    show_name = show_name or session.show_name
    episode_title = episode_title or session.episode_title
    season_number = (
        season_number if season_number is not None else session.season_number
    )
    episode_number = (
        episode_number if episode_number is not None else session.episode_number
    )
    if session.media_type == "episode" and show_name:
        code = (
            f"S{season_number:02d}E{episode_number:02d}"
            if season_number is not None and episode_number is not None
            else None
        )
        parts = [part for part in (show_name, code, episode_title) if part]
        return sanitize_display_name(" - ".join(parts))
    if session.media_type == "movie" and movie_title:
        title = f"{movie_title} ({movie_year})" if movie_year else movie_title
        return sanitize_display_name(title)
    return sanitize_display_name(session.title)


def sanitize_display_name(value: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub(" ", value).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return "Untitled Clip"
    if cleaned.upper() in _RESERVED_NAMES:
        cleaned = f"{cleaned} Clip"
    return cleaned[:160].rstrip(" .") or "Untitled Clip"


def resolve_unique_clip_path(
    clip_root: Path, library: str, title: str, *, exclude: Path | None = None
) -> Path:
    directory = (clip_root / sanitize_display_name(library)).resolve(strict=False)
    base_name = sanitize_display_name(title)
    candidate = directory / f"{base_name}.mp4"
    suffix = 2
    excluded = exclude.resolve(strict=False) if exclude is not None else None
    while candidate.exists() and candidate.resolve(strict=False) != excluded:
        candidate = directory / f"{base_name} - {suffix}.mp4"
        suffix += 1
    if not candidate.resolve(strict=False).is_relative_to(clip_root.resolve(strict=False)):
        raise ValueError("Resolved clip path escaped the configured clip directory.")
    return candidate
