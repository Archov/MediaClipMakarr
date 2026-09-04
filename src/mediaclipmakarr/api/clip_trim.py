"""Read-only media information used by the browser trim editor."""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from mediaclipmakarr.clips import get_clip
from mediaclipmakarr.config import Settings
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

    return router
