"""Durable render planning and validation for edits of managed clips."""

from __future__ import annotations

from datetime import UTC, datetime
from os import stat_result
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from mediaclipmakarr.clip_library import embedded_render_matches
from mediaclipmakarr.config import Settings
from mediaclipmakarr.hdr import HdrCapabilities, planned_hdr_strategy
from mediaclipmakarr.media_renderer import RenderedClipFile
from mediaclipmakarr.render_plan import ClipRenderPlan, render_plan_hash
from mediaclipmakarr.source_media import ResolvedSourceMedia, probe_managed_media_file


class ClipTrimSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    expected_revision: int = Field(ge=1)
    mode: Literal["new", "replace"]


class ClipEditError(RuntimeError):
    job_retryable = False

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.job_error_code = code
        self.job_retryable = retryable


def build_trim_render_plan(
    parent: dict[str, Any],
    request: ClipTrimSaveRequest,
    render_source: ResolvedSourceMedia,
    render_source_stat: stat_result,
    *,
    x264_preset: str,
) -> ClipRenderPlan:
    revision = int(parent["revision"])
    duration_ms = int(parent["duration_ms"])
    if revision != request.expected_revision:
        raise ClipEditError(
            "CLIP_REVISION_CONFLICT",
            f"Clip revision {request.expected_revision} is stale; current revision is {revision}.",
        )
    if request.end_ms <= request.start_ms:
        raise ClipEditError("CLIP_RANGE_ORDER", "End must be later than Start.")
    if request.end_ms > duration_ms:
        raise ClipEditError(
            "CLIP_RANGE_DURATION_EXCEEDED",
            "End must be within the managed clip duration.",
        )

    original_start = int(parent["source_start_ms"]) + request.start_ms
    original_end = int(parent["source_start_ms"]) + request.end_ms
    if original_end > int(parent["source_end_ms"]):
        raise ClipEditError(
            "CLIP_PROVENANCE_RANGE_INVALID",
            "The selected range exceeds the parent clip's original-source range.",
        )

    replacing = request.mode == "replace"
    created_at = _datetime(parent["created_at"])
    hdr = (
        render_source.capabilities.hdr
        if render_source.capabilities is not None
        else HdrCapabilities()
    )
    payload: dict[str, Any] = {
        "job_id": f"job-{uuid4()}",
        "clip_id": str(parent["id"]) if replacing else f"clip-{uuid4()}",
        "revision": revision + 1 if replacing else 1,
        "title": str(parent["title"]),
        "library": str(parent["library"]),
        "media_type": str(parent["media_type"]),
        "custom_title": parent.get("custom_title"),
        "automatic_title": parent.get("automatic_title") or parent["title"],
        "movie_title": parent.get("movie_title"),
        "movie_year": parent.get("movie_year"),
        "show_name": parent.get("show_name"),
        "episode_title": parent.get("episode_title"),
        "season_number": parent.get("season_number"),
        "episode_number": parent.get("episode_number"),
        "clip_number": int(parent.get("clip_number") or 1),
        "session_identity": f"managed-clip:{parent['id']}",
        "media_identity": f"managed-clip-revision:{parent['id']}:{revision}",
        "plex_user": parent.get("plex_username"),
        "source_media": render_source,
        "source_start_ms": request.start_ms,
        "source_end_ms": request.end_ms,
        "selected_audio_stream": render_source.selected_audio_stream,
        "selected_subtitle": render_source.selected_subtitle,
        "hdr": hdr,
        "hdr_strategy": planned_hdr_strategy(hdr),
        "x264_preset": x264_preset,
        "render_plan_hash": "",
        "operation": "trim_replace" if replacing else "trim_new",
        "parent_clip_id": parent.get("parent_clip_id") if replacing else parent["id"],
        "expected_revision": revision,
        "render_source_modified_ns": render_source_stat.st_mtime_ns,
        "provenance_source_path": str(parent["source_path"]),
        "provenance_source_size_bytes": int(parent["source_size_bytes"]),
        "provenance_source_modified_at": _datetime(parent["source_modified_at"]),
        "provenance_start_ms": original_start,
        "provenance_end_ms": original_end,
        "provenance_audio_stream_index": int(parent["selected_audio_stream_index"]),
        "clip_created_at": created_at if replacing else None,
    }
    plan = ClipRenderPlan.model_validate(payload)
    plan.render_plan_hash = render_plan_hash(plan)
    return plan


async def validate_trim_rendered_output(
    rendered: RenderedClipFile,
    plan: ClipRenderPlan,
    settings: Settings,
    *,
    run_blocking,
) -> None:
    """Require playable streams, bounded duration, and exact embedded render identity."""
    try:
        probed = await probe_managed_media_file(
            rendered.path,
            settings,
            run_blocking=run_blocking,
        )
    except Exception as error:
        raise ClipEditError(
            "CLIP_EDIT_OUTPUT_INVALID",
            "The rendered edit could not be validated as playable media.",
            retryable=True,
        ) from error
    expected_duration = plan.source_end_ms - plan.source_start_ms
    actual_duration = probed.duration_ms
    duration_tolerance = max(250, expected_duration // 100)
    if (
        actual_duration is None
        or abs(actual_duration - expected_duration) > duration_tolerance
    ):
        raise ClipEditError(
            "CLIP_EDIT_OUTPUT_INVALID",
            "The rendered edit duration did not match the selected range.",
            retryable=True,
        )
    identity_matches = await run_blocking(
        embedded_render_matches,
        rendered.path,
        plan.clip_id,
        plan.revision,
        plan.render_plan_hash,
    )
    if not identity_matches:
        raise ClipEditError(
            "CLIP_EDIT_OUTPUT_IDENTITY_MISMATCH",
            "The rendered edit did not contain the expected clip revision and render identity.",
            retryable=True,
        )


def _datetime(value: object) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def trim_source_matches(plan: ClipRenderPlan, clip: dict[str, Any], stat: stat_result) -> bool:
    return (
        plan.expected_revision is not None
        and int(clip["revision"]) == plan.expected_revision
        and Path(str(clip["file_path"])).resolve(strict=False)
        == Path(plan.source_media.local_path).resolve(strict=False)
        and stat.st_size == plan.source_media.fingerprint.size_bytes
        and (
            plan.render_source_modified_ns is None
            or stat.st_mtime_ns == plan.render_source_modified_ns
        )
    )
