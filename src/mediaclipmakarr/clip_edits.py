"""Durable render planning and validation for edits of managed clips."""

from __future__ import annotations

from datetime import UTC, datetime
from os import stat_result
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mediaclipmakarr.clip_library import embedded_render_matches
from mediaclipmakarr.config import Settings
from mediaclipmakarr.hdr import HdrCapabilities, planned_hdr_strategy
from mediaclipmakarr.media_renderer import RenderedClipFile
from mediaclipmakarr.render_plan import ClipRenderPlan, render_plan_hash
from mediaclipmakarr.source_media import (
    NO_AUDIO_STREAM_INDEX,
    ResolvedSourceMedia,
    SubtitleSelection,
    probe_managed_media_file,
)


class ClipTrimSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Both allowed to be negative or beyond the managed clip's own duration:
    # once extend has granted room on either side, a selection can sit
    # entirely within that new territory (e.g. both start_ms and end_ms
    # negative, wholly before the original clip's start). Ordering and
    # source-range bounds are enforced by the render-plan builders below,
    # not here.
    start_ms: int
    end_ms: int
    expected_revision: int = Field(ge=1)
    mode: Literal["new", "replace"]
    # Track overrides: omitted/None means "keep whatever the original had".
    # audio_disabled defaults False (not None) since it's a plain flag, so
    # "changed" is judged by track_changed() below, not by field presence.
    audio_stream_index: int | None = None
    audio_disabled: bool = False
    subtitle_stream_index: int | None = None
    subtitles_enabled: bool | None = None


def track_changed(request: ClipTrimSaveRequest) -> bool:
    """True when the request asks for a different audio or subtitle track
    than whatever the clip already has — this alone forces rendering from
    the original source, since the managed clip file only ever contains the
    one audio stream (and burned-in subtitle state, if any) it was created
    with."""
    return (
        request.audio_stream_index is not None
        or request.audio_disabled
        or request.subtitle_stream_index is not None
        or request.subtitles_enabled is not None
    )


def is_extended_trim_request(request: ClipTrimSaveRequest, duration_ms: int) -> bool:
    """True when the request needs the original pristine source rather than
    the already-rendered managed clip file — either because it reaches
    outside the managed clip's own bounds, or because it wants a different
    audio/subtitle track than what's already baked into that file."""
    return request.start_ms < 0 or request.end_ms > duration_ms or track_changed(request)


def recovered_audio_selection(clip: dict[str, Any]) -> tuple[int | None, bool]:
    """The clip's stored audio selection, translated into
    `probe_original_source_media`'s `(requested_stream_index, audio_disabled)`
    contract — `NO_AUDIO_STREAM_INDEX` is a normal, valid stored state (the
    clip was originally rendered with audio off), not an error."""
    stored_index = int(clip["selected_audio_stream_index"])
    if stored_index == NO_AUDIO_STREAM_INDEX:
        return None, True
    return stored_index, False


def recovered_subtitle_selection(embedded: dict[str, Any] | None) -> SubtitleSelection:
    """The clip's original subtitle selection, recovered from its own
    embedded render metadata (the database has no column for this — see
    `read_embedded_render_metadata`). Reused wholesale, including an
    `external_text` strategy with its `external_url` — the renderer already
    knows how to fetch that using the configured Plex token, so there's no
    need to re-derive it from a fresh probe of the (embedded-tracks-only)
    original source."""
    if embedded is None:
        return SubtitleSelection()
    selected = embedded.get("selectedSubtitle")
    if not isinstance(selected, dict):
        return SubtitleSelection()
    try:
        return SubtitleSelection.model_validate(selected)
    except ValidationError:
        return SubtitleSelection()


def source_fingerprint_matches(clip: dict[str, Any], stat: stat_result) -> bool:
    """Whether the original source file at `clip['source_path']` still
    matches what it was when this clip was created — gates whether
    extend/track-change is safe to offer at all."""
    if stat.st_size != int(clip["source_size_bytes"]):
        return False
    return datetime.fromtimestamp(stat.st_mtime, UTC) == _datetime(clip["source_modified_at"])


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
    decode: str = "cpu",
    tonemap: str = "cpu",
    encoder: str = "cpu_x264",
    video_quality: int = 18,
    max_resolution: str = "1080p",
    max_fps: int = 60,
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
        "decode": decode,
        "tonemap": tonemap,
        "encoder": encoder,
        "video_quality": video_quality,
        "max_resolution": max_resolution,
        "max_fps": max_fps,
        # A trim decodes from the already-rendered managed clip file
        # (`probe_managed_media_file`), not the original pristine source —
        # so it's already cropped, scaled, and tonemapped. Applying the
        # parent's crop again here would crop a frame that's already the
        # cropped-and-scaled size, which is invalid whenever the crop box
        # (sized for the original source) exceeds the now-smaller input.
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


def build_extended_trim_render_plan(
    parent: dict[str, Any],
    request: ClipTrimSaveRequest,
    render_source: ResolvedSourceMedia,
    render_source_stat: stat_result,
    *,
    x264_preset: str,
    decode: str = "cpu",
    tonemap: str = "cpu",
    encoder: str = "cpu_x264",
    video_quality: int = 18,
    max_resolution: str = "1080p",
    max_fps: int = 60,
    job_id: str | None = None,
) -> ClipRenderPlan:
    """Like `build_trim_render_plan`, but decodes from the original pristine
    source (`render_source`, probed via `probe_original_source_media`)
    rather than the already-rendered managed clip — used whenever the
    requested range reaches outside the managed clip's own bounds, or a
    different audio/subtitle track was requested (see
    `is_extended_trim_request`). Unlike a normal trim, the parent's crop box
    is reapplied here: the original source is uncropped, unlike the managed
    file a normal trim decodes from.

    `job_id` lets a caller pin the workdir the render lands in (used by the
    fast-preview endpoint, which needs a name it can predict to serve the
    file back by); a real save leaves it unset and gets a fresh random one."""
    revision = int(parent["revision"])
    if revision != request.expected_revision:
        raise ClipEditError(
            "CLIP_REVISION_CONFLICT",
            f"Clip revision {request.expected_revision} is stale; current revision is {revision}.",
        )
    if request.end_ms <= request.start_ms:
        raise ClipEditError("CLIP_RANGE_ORDER", "End must be later than Start.")

    source_start_ms = int(parent["source_start_ms"]) + request.start_ms
    source_end_ms = int(parent["source_start_ms"]) + request.end_ms
    if source_start_ms < 0:
        raise ClipEditError(
            "CLIP_RANGE_BEFORE_SOURCE_START",
            "The selected range starts before the original source begins.",
        )
    if render_source.duration_ms is not None and source_end_ms > render_source.duration_ms:
        raise ClipEditError(
            "CLIP_RANGE_AFTER_SOURCE_END",
            "The selected range extends past the end of the original source.",
        )

    replacing = request.mode == "replace"
    created_at = _datetime(parent["created_at"])
    hdr = (
        render_source.capabilities.hdr
        if render_source.capabilities is not None
        else HdrCapabilities()
    )
    audio_stream_index = (
        render_source.selected_audio_stream.stream_index
        if render_source.selected_audio_stream is not None
        else NO_AUDIO_STREAM_INDEX
    )
    payload: dict[str, Any] = {
        "job_id": job_id or f"job-{uuid4()}",
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
        "source_start_ms": source_start_ms,
        "source_end_ms": source_end_ms,
        "selected_audio_stream": render_source.selected_audio_stream,
        "selected_subtitle": render_source.selected_subtitle,
        "hdr": hdr,
        "hdr_strategy": planned_hdr_strategy(hdr),
        "x264_preset": x264_preset,
        "decode": decode,
        "tonemap": tonemap,
        "encoder": encoder,
        "video_quality": video_quality,
        "max_resolution": max_resolution,
        "max_fps": max_fps,
        # Unlike a normal trim, this decodes straight from the uncropped
        # original source — the parent's crop box must be reapplied, or the
        # render comes out at the wrong framing.
        "crop_width": parent.get("crop_width"),
        "crop_height": parent.get("crop_height"),
        "crop_x": parent.get("crop_x"),
        "crop_y": parent.get("crop_y"),
        "render_plan_hash": "",
        "operation": "trim_replace" if replacing else "trim_new",
        "parent_clip_id": parent.get("parent_clip_id") if replacing else parent["id"],
        "expected_revision": revision,
        "render_source_modified_ns": render_source_stat.st_mtime_ns,
        "provenance_source_path": str(parent["source_path"]),
        "provenance_source_size_bytes": render_source_stat.st_size,
        "provenance_source_modified_at": datetime.fromtimestamp(render_source_stat.st_mtime, UTC),
        "provenance_start_ms": source_start_ms,
        "provenance_end_ms": source_end_ms,
        "provenance_audio_stream_index": audio_stream_index,
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
    """Whether the file the plan decodes from (`stat`, of
    `plan.source_media.local_path` — the managed clip file for a normal
    trim, the original pristine source for an extended one) still matches
    what it was when the plan was built. The clip's own revision is checked
    separately since a normal trim's `local_path` and the managed clip's
    `file_path` are the same file, but an extended trim's are not."""
    return (
        plan.expected_revision is not None
        and int(clip["revision"]) == plan.expected_revision
        and stat.st_size == plan.source_media.fingerprint.size_bytes
        and (
            plan.render_source_modified_ns is None
            or stat.st_mtime_ns == plan.render_source_modified_ns
        )
    )
