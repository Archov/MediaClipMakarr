from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from mediaclipmakarr.clips import ClipCreateRequest
from mediaclipmakarr.plex import PlexSession
from mediaclipmakarr.source_media import MediaStreamIdentity, ResolvedSourceMedia

OutputProfileId = Literal["p1-h264-aac-sdr-v1"]

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

    schema_version: int = 1
    job_id: str
    clip_id: str
    revision: int = 1
    title: str
    library: str
    media_type: str
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
    subtitle_stream_index: int | None = None
    output_profile: OutputProfileId = "p1-h264-aac-sdr-v1"
    x264_preset: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    render_plan_hash: str


def build_clip_render_plan(
    *,
    session: PlexSession,
    request: ClipCreateRequest,
    source_media: ResolvedSourceMedia,
    x264_preset: str,
) -> ClipRenderPlan:
    payload: dict[str, Any] = {
        "job_id": f"job-{uuid4()}",
        "clip_id": f"clip-{uuid4()}",
        "title": sanitize_display_name(session.title),
        "library": default_library_for_media_type(session.media_type),
        "media_type": session.media_type,
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


def sanitize_display_name(value: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub(" ", value).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return "Untitled Clip"
    if cleaned.upper() in _RESERVED_NAMES:
        cleaned = f"{cleaned} Clip"
    return cleaned[:160].rstrip(" .") or "Untitled Clip"


def resolve_unique_clip_path(clip_root: Path, library: str, title: str) -> Path:
    directory = (clip_root / sanitize_display_name(library)).resolve(strict=False)
    base_name = sanitize_display_name(title)
    candidate = directory / f"{base_name}.mp4"
    suffix = 2
    while candidate.exists():
        candidate = directory / f"{base_name} - {suffix}.mp4"
        suffix += 1
    if not candidate.resolve(strict=False).is_relative_to(clip_root.resolve(strict=False)):
        raise ValueError("Resolved clip path escaped the configured clip directory.")
    return candidate
