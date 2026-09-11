"""Read-only media information used by the browser trim editor, plus the
trim/extend save and fast-preview endpoints."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from mediaclipmakarr.clip_edits import (
    ClipEditError,
    ClipTrimSaveRequest,
    build_extended_trim_render_plan,
    build_trim_render_plan,
    is_extended_trim_request,
    recovered_audio_selection,
    recovered_subtitle_selection,
    source_fingerprint_matches,
)
from mediaclipmakarr.clip_library import read_embedded_render_metadata
from mediaclipmakarr.clips import get_clip
from mediaclipmakarr.config import Settings
from mediaclipmakarr.jobs import JobSnapshot, enqueue_clip_create_job
from mediaclipmakarr.media_renderer import SubtitlePreparationError, render_clip_file
from mediaclipmakarr.source_media import (
    ResolvedSourceMedia,
    SourceMediaError,
    TrackDescriptor,
    probe_managed_media_file,
    probe_original_source_media,
)
from mediaclipmakarr.subprocesses import CommandError, CommandResult, run_command

CommandRunner = Callable[..., Awaitable[CommandResult]]

# A throwaway, never-saved profile for the trim/extend preview render — fast
# and low-resolution on purpose, always CPU (no dependency on whatever GPU
# stages the user's real settings might select), independent of the
# application's configured quality settings.
PREVIEW_X264_PRESET = "ultrafast"
PREVIEW_MAX_RESOLUTION = "480p"
PREVIEW_VIDEO_QUALITY = 30
PREVIEW_MAX_FPS = 30


class ClipTrimInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    duration_ms: int
    revision: int
    play_url: str
    frame_rate: float | None = None


class ClipSourceTrackInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    unavailable_reason: str | None = None
    audio_tracks: list[TrackDescriptor] = Field(default_factory=list)
    subtitle_tracks: list[TrackDescriptor] = Field(default_factory=list)
    max_extend_before_ms: int = 0
    max_extend_after_ms: int = 0


class ClipTrimPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_ms: int
    end_ms: int = Field(gt=0)
    audio_stream_index: int | None = None
    audio_disabled: bool = False
    subtitle_stream_index: int | None = None
    subtitles_enabled: bool | None = None
    # Generated client-side (crypto.randomUUID()); constrained to a safe
    # filesystem-path-component shape since it becomes part of a job workdir
    # path.
    preview_token: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9-]+$")


class ClipTrimPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    play_url: str
    duration_ms: int


class ClipMediaProbeError(RuntimeError):
    """Raised when ffprobe cannot return trustworthy clip metadata."""


def parse_frame_rate_ratio(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    numerator, separator, denominator = value.partition("/")
    try:
        num = float(numerator)
        den = float(denominator) if separator else 1.0
    except ValueError:
        return None
    if den == 0:
        return None
    rate = num / den
    return rate if rate > 0 else None


def frame_rate_from_probe(payload: object) -> float | None:
    if not isinstance(payload, dict):
        raise ClipMediaProbeError("ffprobe returned an invalid clip media payload.")
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise ClipMediaProbeError("ffprobe returned no clip video stream metadata.")
    stream = next((item for item in streams if isinstance(item, dict)), None)
    if stream is None:
        return None
    # This is a nominal duration for navigation controls, not a promise that a
    # millisecond timestamp lands on an exact decoded frame for VFR media.
    return parse_frame_rate_ratio(stream.get("avg_frame_rate")) or parse_frame_rate_ratio(
        stream.get("r_frame_rate")
    )


async def probe_clip_frame_rate(
    path: Path,
    settings: Settings,
    *,
    runner: CommandRunner = run_command,
) -> float | None:
    try:
        result = await runner(
            [
                settings.ffprobe_path,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=avg_frame_rate,r_frame_rate",
                "-of",
                "json",
                os.fspath(path),
            ],
            timeout_seconds=settings.subprocess_timeout_seconds,
        )
    except CommandError as error:
        raise ClipMediaProbeError("ffprobe could not inspect the managed clip.") from error
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ClipMediaProbeError("ffprobe returned invalid clip media metadata.") from error
    return frame_rate_from_probe(payload)


async def _ignore_progress(_progress: float, _message: str) -> None:
    return None


async def _resolve_extended_render_source(
    clip: dict[str, Any],
    *,
    audio_stream_index: int | None,
    audio_disabled: bool,
    subtitle_stream_index: int | None,
    subtitles_enabled: bool | None,
    settings: Settings,
    run_blocking,
) -> tuple[ResolvedSourceMedia, os.stat_result]:
    """Resolve the original source for an extend/track-change request —
    shared by the preview and save endpoints. Raises `SourceMediaError` if
    the original source is missing or no longer matches what this clip was
    created from."""
    source_path = Path(str(clip["source_path"]))
    try:
        stat = await run_blocking(source_path.stat)
    except OSError as error:
        raise SourceMediaError(
            "SOURCE_PATH_MISSING",
            "The original source media is no longer available.",
            retryable=True,
        ) from error
    if not source_fingerprint_matches(clip, stat):
        raise SourceMediaError(
            "SOURCE_MEDIA_CHANGED",
            "The original source media has changed since this clip was created.",
        )

    audio_changed = audio_stream_index is not None or audio_disabled
    if audio_changed:
        requested_audio_index, resolved_audio_disabled = audio_stream_index, audio_disabled
    else:
        requested_audio_index, resolved_audio_disabled = recovered_audio_selection(clip)

    subtitle_changed = subtitle_stream_index is not None or subtitles_enabled is not None
    embedded = await run_blocking(
        read_embedded_render_metadata,
        Path(str(clip["file_path"])),
        str(clip["id"]),
        int(clip["revision"]),
    )
    render_source = await probe_original_source_media(
        source_path,
        settings,
        run_blocking=run_blocking,
        requested_audio_stream_index=requested_audio_index,
        audio_disabled=resolved_audio_disabled,
        requested_subtitle_stream_index=subtitle_stream_index if subtitle_changed else None,
        subtitles_enabled=(subtitles_enabled or False) if subtitle_changed else False,
    )
    if not subtitle_changed:
        render_source = render_source.model_copy(
            update={"selected_subtitle": recovered_subtitle_selection(embedded)}
        )
    return render_source, stat


def build_router(application_settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/api/clips/{clip_id}/trim-info", response_model=ClipTrimInfo)
    async def clip_trim_info(clip_id: str, request: Request) -> ClipTrimInfo:
        clip = await get_clip(
            request.app.state.database_engine,
            clip_id,
            application_settings.resolved_clip_dir,
        )
        if clip is None:
            raise HTTPException(status_code=404, detail="Clip not found.")
        try:
            frame_rate = await probe_clip_frame_rate(
                Path(str(clip["file_path"])), application_settings
            )
        except ClipMediaProbeError as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "CLIP_MEDIA_PROBE_FAILED",
                    "message": str(error),
                    "retryable": True,
                },
            ) from error
        return ClipTrimInfo(
            id=str(clip["id"]),
            title=str(clip["title"]),
            duration_ms=int(clip["duration_ms"]),
            revision=int(clip["revision"]),
            play_url=f"/api/clips/{clip_id}/media",
            frame_rate=frame_rate,
        )

    @router.get("/api/clips/{clip_id}/source-tracks", response_model=ClipSourceTrackInfo)
    async def clip_source_tracks(clip_id: str, request: Request) -> ClipSourceTrackInfo:
        clip = await get_clip(
            request.app.state.database_engine,
            clip_id,
            application_settings.resolved_clip_dir,
        )
        if clip is None:
            raise HTTPException(status_code=404, detail="Clip not found.")

        requested_audio_index, audio_disabled = recovered_audio_selection(clip)
        embedded = await request.app.state.blocking_io.run(
            read_embedded_render_metadata,
            Path(str(clip["file_path"])),
            str(clip["id"]),
            int(clip["revision"]),
        )
        recovered_subtitle = recovered_subtitle_selection(embedded)
        # Only an embedded-track selection can be marked "selected" against
        # the enumerated list below — an external sidecar (or no selection)
        # just leaves nothing marked, which is fine: the picker's default
        # for "unchanged" doesn't depend on it being present in this list.
        embedded_subtitle_index = (
            recovered_subtitle.stream.stream_index
            if recovered_subtitle.enabled and recovered_subtitle.stream is not None
            else None
        )

        try:
            render_source, _stat = await _resolve_extended_render_source(
                clip,
                audio_stream_index=requested_audio_index,
                audio_disabled=audio_disabled,
                subtitle_stream_index=embedded_subtitle_index,
                subtitles_enabled=embedded_subtitle_index is not None,
                settings=application_settings,
                run_blocking=request.app.state.blocking_io.run,
            )
        except SourceMediaError as error:
            return ClipSourceTrackInfo(available=False, unavailable_reason=error.message)

        capabilities = render_source.capabilities
        source_duration_ms = render_source.duration_ms or 0
        return ClipSourceTrackInfo(
            available=True,
            audio_tracks=capabilities.audio_tracks if capabilities else [],
            subtitle_tracks=capabilities.subtitle_tracks if capabilities else [],
            max_extend_before_ms=int(clip["source_start_ms"]),
            max_extend_after_ms=max(0, source_duration_ms - int(clip["source_end_ms"])),
        )

    @router.post("/api/clips/{clip_id}/trim-preview", response_model=ClipTrimPreviewResponse)
    async def create_trim_preview(
        clip_id: str, preview: ClipTrimPreviewRequest, request: Request
    ) -> ClipTrimPreviewResponse:
        clip = await get_clip(
            request.app.state.database_engine,
            clip_id,
            application_settings.resolved_clip_dir,
        )
        if clip is None:
            raise HTTPException(status_code=404, detail="Clip not found.")
        if preview.end_ms <= preview.start_ms:
            raise HTTPException(status_code=422, detail="end_ms must be greater than start_ms.")

        try:
            render_source, source_stat = await _resolve_extended_render_source(
                clip,
                audio_stream_index=preview.audio_stream_index,
                audio_disabled=preview.audio_disabled,
                subtitle_stream_index=preview.subtitle_stream_index,
                subtitles_enabled=preview.subtitles_enabled,
                settings=application_settings,
                run_blocking=request.app.state.blocking_io.run,
            )
            saveish = ClipTrimSaveRequest(
                start_ms=preview.start_ms,
                end_ms=preview.end_ms,
                expected_revision=int(clip["revision"]),
                mode="new",
                audio_stream_index=preview.audio_stream_index,
                audio_disabled=preview.audio_disabled,
                subtitle_stream_index=preview.subtitle_stream_index,
                subtitles_enabled=preview.subtitles_enabled,
            )
            plan = build_extended_trim_render_plan(
                clip,
                saveish,
                render_source,
                source_stat,
                x264_preset=PREVIEW_X264_PRESET,
                decode="cpu",
                tonemap="cpu",
                encoder="cpu_x264",
                video_quality=PREVIEW_VIDEO_QUALITY,
                max_resolution=PREVIEW_MAX_RESOLUTION,
                max_fps=PREVIEW_MAX_FPS,
                job_id=f"trim-preview-{preview.preview_token}",
            )
            async with request.app.state.media_process_gate.slot():
                rendered = await render_clip_file(
                    plan, application_settings, progress=_ignore_progress
                )
        except ClipEditError as error:
            raise HTTPException(
                status_code=409 if error.job_error_code == "CLIP_REVISION_CONFLICT" else 422,
                detail={
                    "code": error.job_error_code,
                    "message": str(error),
                    "retryable": error.job_retryable,
                },
            ) from error
        except SourceMediaError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail={
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                    "alternatives": error.alternatives,
                },
            ) from error
        except (CommandError, SubtitlePreparationError) as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "TRIM_PREVIEW_RENDER_FAILED",
                    "message": str(error),
                    "retryable": True,
                },
            ) from error
        return ClipTrimPreviewResponse(
            play_url=f"/api/clips/{clip_id}/trim-preview/{preview.preview_token}",
            duration_ms=rendered.duration_ms,
        )

    @router.get("/api/clips/{clip_id}/trim-preview/{token}")
    async def get_trim_preview(clip_id: str, token: str) -> FileResponse:
        path = application_settings.resolved_work_dir / "jobs" / f"trim-preview-{token}" / "rendered.mp4"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Preview not found.")
        return FileResponse(
            path,
            media_type="video/mp4",
            headers={"Cache-Control": "no-store"},
        )

    @router.delete("/api/clips/{clip_id}/trim-preview/{token}", status_code=204)
    async def delete_trim_preview(clip_id: str, token: str, request: Request) -> Response:
        workdir = application_settings.resolved_work_dir / "jobs" / f"trim-preview-{token}"

        def cleanup() -> None:
            shutil.rmtree(workdir, ignore_errors=True)

        await request.app.state.blocking_io.run(cleanup)
        return Response(status_code=204)

    @router.post("/api/clips/{clip_id}/trim", response_model=JobSnapshot)
    async def save_trim(
        clip_id: str, trim: ClipTrimSaveRequest, request: Request
    ) -> JobSnapshot:
        clip = await get_clip(
            request.app.state.database_engine,
            clip_id,
            application_settings.resolved_clip_dir,
        )
        if clip is None:
            raise HTTPException(status_code=404, detail="Clip not found.")
        try:
            if is_extended_trim_request(trim, int(clip["duration_ms"])):
                render_source, source_stat = await _resolve_extended_render_source(
                    clip,
                    audio_stream_index=trim.audio_stream_index,
                    audio_disabled=trim.audio_disabled,
                    subtitle_stream_index=trim.subtitle_stream_index,
                    subtitles_enabled=trim.subtitles_enabled,
                    settings=application_settings,
                    run_blocking=request.app.state.blocking_io.run,
                )
                plan = build_extended_trim_render_plan(
                    clip,
                    trim,
                    render_source,
                    source_stat,
                    x264_preset=request.app.state.effective_application_settings.x264_preset,
                    decode=request.app.state.effective_application_settings.video_decode,
                    tonemap=request.app.state.effective_application_settings.video_tonemap,
                    encoder=request.app.state.effective_application_settings.video_encoder,
                    video_quality=request.app.state.effective_application_settings.video_quality,
                    max_resolution=request.app.state.effective_application_settings.video_max_resolution,
                    max_fps=request.app.state.effective_application_settings.video_max_fps,
                )
            else:
                path = Path(str(clip["file_path"]))
                render_source = await probe_managed_media_file(
                    path,
                    application_settings,
                    run_blocking=request.app.state.blocking_io.run,
                )
                source_stat = await request.app.state.blocking_io.run(path.stat)
                plan = build_trim_render_plan(
                    clip,
                    trim,
                    render_source,
                    source_stat,
                    x264_preset=request.app.state.effective_application_settings.x264_preset,
                    decode=request.app.state.effective_application_settings.video_decode,
                    tonemap=request.app.state.effective_application_settings.video_tonemap,
                    encoder=request.app.state.effective_application_settings.video_encoder,
                    video_quality=request.app.state.effective_application_settings.video_quality,
                    max_resolution=request.app.state.effective_application_settings.video_max_resolution,
                    max_fps=request.app.state.effective_application_settings.video_max_fps,
                )
            job = await enqueue_clip_create_job(request.app.state.database_engine, plan)
        except ClipEditError as error:
            raise HTTPException(
                status_code=409 if error.job_error_code == "CLIP_REVISION_CONFLICT" else 422,
                detail={
                    "code": error.job_error_code,
                    "message": str(error),
                    "retryable": error.job_retryable,
                },
            ) from error
        except SourceMediaError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail={
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                    "alternatives": error.alternatives,
                },
            ) from error
        await request.app.state.job_events.publish(job.id, job)
        request.app.state.job_runner.wake()
        return job

    return router
