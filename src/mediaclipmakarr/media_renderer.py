from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from mediaclipmakarr.config import Settings
from mediaclipmakarr.render_plan import ClipRenderPlan
from mediaclipmakarr.subprocesses import CommandError, CommandFailedError, run_command

ProgressCallback = Callable[[float, str], Awaitable[None]]

TEXT_SUBTITLE_PREROLL_MS = 5_000
BITMAP_SUBTITLE_PROBE_WINDOW_MS = 15_000


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
    subtitle_preroll_ms = await _subtitle_preroll_ms(plan, settings)
    argv = build_ffmpeg_clip_args(
        plan,
        settings,
        output_path,
        subtitle_preroll_ms=subtitle_preroll_ms,
        work_dir=output_dir,
    )
    await _run_ffmpeg_with_progress(argv, duration_ms=duration_ms, progress=progress)
    return RenderedClipFile(path=output_path, duration_ms=duration_ms)


def _prepare_output_path(output_dir: Path, output_path: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def build_ffmpeg_clip_args(
    plan: ClipRenderPlan,
    settings: Settings,
    output_path: Path,
    *,
    subtitle_preroll_ms: int = 0,
    work_dir: Path | None = None,
) -> list[str]:
    duration_seconds = (plan.source_end_ms - plan.source_start_ms) / 1000
    decode_start_ms = max(0, plan.source_start_ms - subtitle_preroll_ms)
    preroll_seconds = (plan.source_start_ms - decode_start_ms) / 1000
    start_seconds = decode_start_ms / 1000
    input_duration_seconds = duration_seconds + preroll_seconds
    metadata = _metadata_envelope(plan)
    audio_stream = plan.selected_audio_stream
    argv = [
        os.fspath(settings.ffmpeg_path),
        "-hide_banner",
        "-y",
        "-ss",
        f"{start_seconds:.3f}",
        "-i",
        plan.source_media.local_path,
        "-t",
        f"{input_duration_seconds:.3f}",
    ]

    subtitle_filter = _subtitle_video_filter(plan, preroll_seconds, work_dir)
    if subtitle_filter.complex_filter:
        argv.extend(["-filter_complex", subtitle_filter.filter_value])
    else:
        argv.extend(
            [
                "-vf",
                subtitle_filter.filter_value,
                "-af",
                _audio_filter(preroll_seconds),
            ]
        )

    argv.extend(
        [
        "-map",
        subtitle_filter.video_map,
        "-map",
        subtitle_filter.audio_map or f"0:{audio_stream.stream_index}",
        "-sn",
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
    )
    return argv


@dataclass(frozen=True, slots=True)
class VideoFilterPlan:
    filter_value: str
    video_map: str
    audio_map: str | None = None
    complex_filter: bool = False


def _subtitle_video_filter(
    plan: ClipRenderPlan, preroll_seconds: float, work_dir: Path | None
) -> VideoFilterPlan:
    base = (
        "scale=w='min(1920,iw)':h='min(1080,ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2,format=yuv420p"
    )
    trim = (
        f"trim=start={preroll_seconds:.3f}:"
        f"duration={_duration_seconds(plan):.3f},setpts=PTS-STARTPTS"
    )
    strategy = plan.selected_subtitle.strategy
    stream = plan.selected_subtitle.stream
    if strategy == "embedded_text" and stream is not None:
        source = _filter_path(plan.source_media.local_path)
        fonts_dir = _font_attachments_dir(work_dir)
        fonts_arg = f":fontsdir={_filter_path(os.fspath(fonts_dir))}" if fonts_dir else ""
        return VideoFilterPlan(
            f"{base},subtitles={source}:si={stream.stream_index}{fonts_arg},{trim}",
            f"0:{plan.source_media.video_streams[0].stream_index}",
        )
    if strategy == "bitmap" and stream is not None:
        filter_value = (
            f"[0:{plan.source_media.video_streams[0].stream_index}]"
            f"{base},setpts=PTS-STARTPTS[v];"
            f"[0:{stream.stream_index}]setpts=PTS-STARTPTS[s];"
            f"[v][s]overlay,format=yuv420p,{trim}[outv];"
            f"[0:{plan.selected_audio_stream.stream_index}]"
            f"{_audio_filter(preroll_seconds)}[outa]"
        )
        return VideoFilterPlan(filter_value, "[outv]", "[outa]", complex_filter=True)
    return VideoFilterPlan(
        f"{base},{trim}",
        f"0:{plan.source_media.video_streams[0].stream_index}",
    )


def _audio_filter(preroll_seconds: float) -> str:
    return f"atrim=start={preroll_seconds:.3f},asetpts=PTS-STARTPTS"


def _duration_seconds(plan: ClipRenderPlan) -> float:
    return (plan.source_end_ms - plan.source_start_ms) / 1000


def _filter_path(path: str) -> str:
    escaped = path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return f"'{escaped}'"


def _font_attachments_dir(work_dir: Path | None) -> Path | None:
    if work_dir is None:
        return None
    fonts_dir = work_dir / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    return fonts_dir


async def _subtitle_preroll_ms(plan: ClipRenderPlan, settings: Settings) -> int:
    if not plan.selected_subtitle.enabled:
        return 0
    if plan.selected_subtitle.strategy == "embedded_text":
        return min(TEXT_SUBTITLE_PREROLL_MS, plan.source_start_ms)
    if plan.selected_subtitle.strategy != "bitmap":
        return 0
    return min(
        await _bitmap_packet_preroll_ms(plan, settings),
        plan.source_start_ms,
    )


async def _bitmap_packet_preroll_ms(plan: ClipRenderPlan, settings: Settings) -> int:
    stream = plan.selected_subtitle.stream
    if stream is None:
        return 0
    window_start_ms = max(0, plan.source_start_ms - BITMAP_SUBTITLE_PROBE_WINDOW_MS)
    start = window_start_ms / 1000
    end = (plan.source_start_ms + 1_000) / 1000
    result = await run_command(
        [
            settings.ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            f"0:{stream.stream_index}",
            "-show_packets",
            "-show_entries",
            "packet=pts_time,dts_time,duration_time,flags",
            "-read_intervals",
            f"{start:.3f}%{end:.3f}",
            "-of",
            "json",
            plan.source_media.local_path,
        ],
        timeout_seconds=settings.subprocess_timeout_seconds,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise CommandError(
            "ffprobe returned invalid packet metadata for bitmap subtitles."
        ) from error
    packets = payload.get("packets")
    if not isinstance(packets, list):
        raise CommandError("ffprobe returned no packet metadata for bitmap subtitles.")
    packet_times = [
        _packet_time_ms(packet)
        for packet in packets
        if isinstance(packet, dict) and _packet_time_ms(packet) is not None
    ]
    if not packet_times:
        raise CommandError("Bitmap subtitle preroll could not be determined from packet metadata.")
    earliest = min(packet_times)
    return max(0, plan.source_start_ms - earliest)


def _packet_time_ms(packet: dict[str, object]) -> int | None:
    for key in ("pts_time", "dts_time"):
        value = packet.get(key)
        if value is None:
            continue
        try:
            return max(0, round(float(str(value)) * 1000))
        except ValueError:
            continue
    return None


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
        "selectedSubtitle": plan.selected_subtitle.model_dump(mode="json"),
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
