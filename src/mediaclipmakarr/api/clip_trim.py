"""Read-only media information used by the browser trim editor."""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from mediaclipmakarr.clip_edits import (
    ClipEditError,
    ClipTrimSaveRequest,
    build_trim_render_plan,
)
from mediaclipmakarr.clips import get_clip
from mediaclipmakarr.config import Settings
from mediaclipmakarr.jobs import JobSnapshot, enqueue_clip_create_job
from mediaclipmakarr.source_media import SourceMediaError, probe_managed_media_file
from mediaclipmakarr.subprocesses import CommandError, CommandResult, run_command

CommandRunner = Callable[..., Awaitable[CommandResult]]


class ClipTrimInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    duration_ms: int
    revision: int
    play_url: str
    frame_rate: float | None = None


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
        path = Path(str(clip["file_path"]))
        try:
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
