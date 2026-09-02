"""Subtitle-free still-frame rendering for live Plex sessions."""

from __future__ import annotations

import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from mediaclipmakarr.application_settings import EffectiveApplicationSettings
from mediaclipmakarr.config import Settings
from mediaclipmakarr.hdr import AdvancedMediaError, HdrCapabilities, planned_hdr_strategy
from mediaclipmakarr.plex import PlexSession
from mediaclipmakarr.source_media import BlockingRunner, resolve_media_capabilities
from mediaclipmakarr.subprocesses import CommandError, CommandResult, run_command
from mediaclipmakarr.video_filters import build_video_frame_filter

FrameVariant = Literal["thumbnail", "export"]
CommandRunner = Callable[..., Awaitable[CommandResult]]


@dataclass(frozen=True, slots=True)
class RenderedSessionFrame:
    path: Path
    work_dir: Path
    filename: str


class SessionFrameError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        retryable: bool = False,
        context: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.context = context or {}


async def render_session_frame(
    session: PlexSession,
    position_ms: int,
    variant: FrameVariant,
    effective_settings: EffectiveApplicationSettings,
    settings: Settings,
    *,
    run_blocking: BlockingRunner,
    runner: CommandRunner = run_command,
) -> RenderedSessionFrame:
    if position_ms < 0:
        raise SessionFrameError(
            "FRAME_POSITION_INVALID",
            "The frame position cannot be negative.",
            status_code=422,
        )

    source = await resolve_media_capabilities(
        session,
        effective_settings,
        settings,
        run_blocking=run_blocking,
        runner=runner,
    )
    duration_ms = source.duration_ms or session.duration_ms
    if duration_ms is not None and position_ms > duration_ms:
        raise SessionFrameError(
            "FRAME_POSITION_INVALID",
            "The requested frame is beyond the end of the selected media.",
            status_code=422,
            context={"duration_ms": duration_ms},
        )
    if not source.video_streams or source.capabilities is None:
        raise SessionFrameError(
            "VIDEO_STREAM_UNAVAILABLE",
            "The selected source does not contain a usable video stream.",
        )

    work_dir, output_path = await run_blocking(
        _prepare_frame_output,
        settings.resolved_work_dir,
        variant,
    )
    try:
        argv = build_ffmpeg_frame_args(
            source.local_path,
            source.video_streams[0].stream_index,
            source.capabilities.hdr,
            position_ms,
            variant,
            settings,
            output_path,
        )
        await runner(
            argv,
            timeout_seconds=settings.media_preparation_timeout_seconds,
            cwd=work_dir,
        )
        await run_blocking(_validate_rendered_frame, output_path)
    except AdvancedMediaError as error:
        await run_blocking(cleanup_session_frame_work_dir, work_dir)
        raise SessionFrameError(
            error.job_error_code,
            str(error),
            context=error.context,
        ) from error
    except CommandError as error:
        await run_blocking(cleanup_session_frame_work_dir, work_dir)
        raise SessionFrameError(
            "FRAME_RENDER_FAILED",
            "FFmpeg could not render the requested frame.",
            retryable=True,
        ) from error
    except BaseException:
        await run_blocking(cleanup_session_frame_work_dir, work_dir)
        raise

    filename = f"frame-{position_ms}ms.png"
    return RenderedSessionFrame(path=output_path, work_dir=work_dir, filename=filename)


def build_ffmpeg_frame_args(
    source_path: str,
    video_stream_index: int,
    hdr: HdrCapabilities,
    position_ms: int,
    variant: FrameVariant,
    settings: Settings,
    output_path: Path,
) -> list[str]:
    strategy = planned_hdr_strategy(hdr)
    dimensions = {"max_width": 480, "max_height": 270} if variant == "thumbnail" else {}
    video_filter = build_video_frame_filter(hdr, strategy, **dimensions)
    return [
        os.fspath(settings.ffmpeg_path),
        "-hide_banner",
        "-y",
        "-ss",
        f"{position_ms / 1000:.3f}",
        "-i",
        source_path,
        "-map",
        f"0:{video_stream_index}",
        "-vf",
        video_filter,
        "-frames:v",
        "1",
        "-an",
        "-sn",
        "-dn",
        "-c:v",
        "png",
        "-compression_level",
        "4",
        "-update",
        "1",
        os.fspath(output_path),
    ]


def _prepare_frame_output(work_root: Path, variant: FrameVariant) -> tuple[Path, Path]:
    frame_root = work_root / "session-frames"
    work_dir = frame_root / f"frame-{uuid4()}"
    if not work_dir.is_relative_to(work_root):
        raise ValueError("Session frame work path escaped the configured work directory.")
    work_dir.mkdir(parents=True, exist_ok=False)
    return work_dir, work_dir / f"{variant}.png"


def _validate_rendered_frame(output_path: Path) -> None:
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise SessionFrameError(
            "FRAME_RENDER_FAILED",
            "FFmpeg did not produce the requested frame.",
            retryable=True,
        )


def cleanup_session_frame_work_dir(work_dir: Path) -> None:
    shutil.rmtree(work_dir, ignore_errors=True)
