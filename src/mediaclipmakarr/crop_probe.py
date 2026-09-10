"""Letterbox/pillarbox crop detection for a live Plex session's source file."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from mediaclipmakarr.application_settings import EffectiveApplicationSettings
from mediaclipmakarr.config import Settings
from mediaclipmakarr.crop_detect import (
    build_cropdetect_probe_args,
    combine_crop_margins,
    crop_box_for_aspect_ratio,
    crop_filter_args,
    format_aspect_ratio,
    parse_aspect_ratio,
    parse_cropdetect_output,
)
from mediaclipmakarr.plex import PlexSession
from mediaclipmakarr.source_media import BlockingRunner, resolve_media_capabilities
from mediaclipmakarr.subprocesses import CommandError, CommandResult, run_command

CommandRunner = Callable[..., Awaitable[CommandResult]]

# Each probe samples this many seconds starting at its offset — enough frames
# (at any real-world frame rate) for cropdetect's own accumulating detection
# (reset_count=0 by default) to converge on a stable, conservative result.
_PROBE_SECONDS = 2.0
# How far in from each end of the selected range to sample, so the probe
# lands on ordinary content rather than a fade-in/fade-out or a title card.
_PROBE_OFFSET_SECONDS = 5.0


class CropProbeError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class CropDetectionResult:
    source_width: int
    source_height: int
    crop_width: int | None
    crop_height: int | None
    crop_x: int | None
    crop_y: int | None
    aspect_ratio_fraction: str | None
    aspect_ratio_decimal: str | None


async def detect_crop_for_session(
    session: PlexSession,
    start_ms: int,
    end_ms: int,
    aspect_ratio: str | None,
    effective_settings: EffectiveApplicationSettings,
    settings: Settings,
    *,
    run_blocking: BlockingRunner,
    runner: CommandRunner = run_command,
) -> CropDetectionResult:
    if end_ms <= start_ms:
        raise CropProbeError(
            "CROP_RANGE_INVALID", "End must be later than Start.", status_code=422
        )
    source = await resolve_media_capabilities(
        session, effective_settings, settings, run_blocking=run_blocking, runner=runner
    )
    if not source.video_streams:
        raise CropProbeError(
            "VIDEO_STREAM_UNAVAILABLE", "The selected source does not contain a usable video stream."
        )
    video = source.video_streams[0]
    if video.width is None or video.height is None:
        raise CropProbeError(
            "VIDEO_STREAM_UNAVAILABLE", "The selected source's frame dimensions are unknown."
        )
    in_width, in_height = video.width, video.height

    if aspect_ratio is not None and aspect_ratio.strip().casefold() != "auto":
        try:
            ratio = parse_aspect_ratio(aspect_ratio)
        except ValueError as error:
            raise CropProbeError(
                "CROP_ASPECT_RATIO_INVALID", str(error), status_code=422
            ) from error
        box = crop_box_for_aspect_ratio(ratio, in_width=in_width, in_height=in_height)
    else:
        box = await _auto_detect_crop_box(
            source.local_path,
            start_ms,
            end_ms,
            in_width,
            in_height,
            settings,
            runner=runner,
        )

    if box is None:
        return CropDetectionResult(
            source_width=in_width,
            source_height=in_height,
            crop_width=None,
            crop_height=None,
            crop_x=None,
            crop_y=None,
            aspect_ratio_fraction=None,
            aspect_ratio_decimal=None,
        )
    width, height, x, y = box
    fraction, decimal = format_aspect_ratio(width, height)
    return CropDetectionResult(
        source_width=in_width,
        source_height=in_height,
        crop_width=width,
        crop_height=height,
        crop_x=x,
        crop_y=y,
        aspect_ratio_fraction=fraction,
        aspect_ratio_decimal=decimal,
    )


async def _auto_detect_crop_box(
    local_path: str,
    start_ms: int,
    end_ms: int,
    in_width: int,
    in_height: int,
    settings: Settings,
    *,
    runner: CommandRunner,
) -> tuple[int, int, int, int] | None:
    start_seconds = start_ms / 1000
    end_seconds = end_ms / 1000
    first = start_seconds + _PROBE_OFFSET_SECONDS
    second = max(first, end_seconds - _PROBE_OFFSET_SECONDS)
    offsets = {first, second}

    samples = []
    for offset in offsets:
        args = build_cropdetect_probe_args(
            local_path, start_seconds=offset, probe_seconds=_PROBE_SECONDS
        )
        args[0] = os.fspath(settings.ffmpeg_path)
        try:
            result = await runner(
                args, timeout_seconds=settings.media_preparation_timeout_seconds, check=False
            )
        except CommandError as error:
            raise CropProbeError(
                "CROP_DETECT_FAILED", "FFmpeg could not analyze the source for crop bars.", retryable=True
            ) from error
        margins = parse_cropdetect_output(result.stderr, in_width=in_width, in_height=in_height)
        if margins is not None:
            samples.append(margins)

    combined = combine_crop_margins(samples)
    if combined is None:
        return None
    return crop_filter_args(combined, in_width=in_width, in_height=in_height)
