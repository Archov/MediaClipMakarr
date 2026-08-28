from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from mediaclipmakarr.config import Settings
from mediaclipmakarr.render_plan import ClipRenderPlan
from mediaclipmakarr.subprocesses import CommandError, CommandFailedError

ProgressCallback = Callable[[float, str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RenderedClipFile:
    path: Path
    duration_ms: int


async def render_clip_file(
    plan: ClipRenderPlan,
    settings: Settings,
    *,
    progress: ProgressCallback,
) -> RenderedClipFile:
    output_dir = settings.resolved_work_dir / "jobs" / plan.job_id
    output_path = output_dir / "rendered.mp4"
    await asyncio.to_thread(_prepare_output_path, output_dir, output_path)

    duration_ms = plan.source_end_ms - plan.source_start_ms
    argv = build_ffmpeg_clip_args(plan, settings, output_path)
    await _run_ffmpeg_with_progress(argv, duration_ms=duration_ms, progress=progress)
    return RenderedClipFile(path=output_path, duration_ms=duration_ms)


def _prepare_output_path(output_dir: Path, output_path: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()


def build_ffmpeg_clip_args(
    plan: ClipRenderPlan, settings: Settings, output_path: Path
) -> list[str]:
    duration_seconds = (plan.source_end_ms - plan.source_start_ms) / 1000
    start_seconds = plan.source_start_ms / 1000
    metadata = _metadata_envelope(plan)
    video_stream = plan.source_media.video_streams[0]
    audio_stream = plan.selected_audio_stream
    return [
        os.fspath(settings.ffmpeg_path),
        "-hide_banner",
        "-y",
        "-ss",
        f"{start_seconds:.3f}",
        "-i",
        plan.source_media.local_path,
        "-t",
        f"{duration_seconds:.3f}",
        "-map",
        f"0:{video_stream.stream_index}",
        "-map",
        f"0:{audio_stream.stream_index}",
        "-sn",
        "-vf",
        (
            "scale=w='min(1920,iw)':h='min(1080,ih)':"
            "force_original_aspect_ratio=decrease:force_divisible_by=2,format=yuv420p"
        ),
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-preset",
        plan.x264_preset,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        "-metadata",
        f"title={plan.title}",
        "-metadata",
        f"comment={metadata}",
        "-progress",
        "pipe:1",
        "-nostats",
        os.fspath(output_path),
    ]


def _metadata_envelope(plan: ClipRenderPlan) -> str:
    payload = {
        "schemaVersion": 1,
        "application": "MediaClipMakarr",
        "clipId": plan.clip_id,
        "revision": plan.revision,
        "title": plan.title,
        "library": plan.library,
        "source": {
            "path": plan.source_media.local_path,
            "startMs": plan.source_start_ms,
            "endMs": plan.source_end_ms,
            "fingerprint": plan.source_media.fingerprint.model_dump(mode="json"),
        },
        "selectedAudioStream": plan.selected_audio_stream.model_dump(mode="json"),
        "renderProfile": plan.output_profile,
        "renderPlanHash": plan.render_plan_hash,
    }
    return "MediaClipMakarr " + json.dumps(payload, sort_keys=True, separators=(",", ":"))


async def _run_ffmpeg_with_progress(
    argv: list[str],
    *,
    duration_ms: int,
    progress: ProgressCallback,
) -> None:
    await progress(0.0, "Starting FFmpeg.")
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise CommandError(f"FFmpeg could not be started ({error.strerror}).") from error

    stderr_task = asyncio.create_task(process.stderr.read() if process.stderr else _empty_bytes())
    try:
        while process.stdout is not None and not process.stdout.at_eof():
            raw_line = await process.stdout.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            key, separator, value = line.partition("=")
            if separator and key == "out_time_ms":
                await progress(_progress_from_ffmpeg_time(value, duration_ms), "Rendering video.")
            elif separator and key == "progress" and value == "end":
                await progress(1.0, "FFmpeg render completed.")

        returncode = await process.wait()
        stderr = (await stderr_task).decode("utf-8", errors="replace")
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        await asyncio.gather(stderr_task, return_exceptions=True)
        raise

    if returncode != 0:
        raise CommandFailedError(argv[0], returncode, stderr)


async def _empty_bytes() -> bytes:
    return b""


def _progress_from_ffmpeg_time(value: str, duration_ms: int) -> float:
    if duration_ms <= 0:
        return 0.0
    try:
        rendered_ms = int(value) / 1000
    except ValueError:
        return 0.0
    return min(1.0, max(0.0, rendered_ms / duration_ms))
