from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from mediaclipmakarr.config import Settings
from mediaclipmakarr.render_plan import ClipRenderPlan
from mediaclipmakarr.subprocesses import CommandError, CommandFailedError, run_command
from mediaclipmakarr.video_filters import build_video_base_filter, output_color_args

ProgressCallback = Callable[[float, str], Awaitable[None]]
logger = logging.getLogger(__name__)

TEXT_SUBTITLE_PREROLL_MS = 30_000
BITMAP_SUBTITLE_PROBE_WINDOW_MS = 15_000
SUPPORTED_FONT_ATTACHMENT_CODECS = {"ttf", "otf", "ttc", "woff", "woff2"}
SUPPORTED_FONT_ATTACHMENT_EXTENSIONS = {".ttf", ".otf", ".ttc", ".woff", ".woff2"}
SUPPORTED_FONT_ATTACHMENT_MIME_TYPES = {
    "application/font-sfnt",
    "application/font-woff",
    "application/font-woff2",
    "application/vnd.ms-opentype",
    "application/x-font-ttf",
    "application/x-truetype-font",
    "font/collection",
    "font/otf",
    "font/ttf",
    "font/woff",
    "font/woff2",
}


@dataclass(frozen=True, slots=True)
class RenderedClipFile:
    path: Path
    duration_ms: int


@dataclass(frozen=True, slots=True)
class PreparedTextSubtitle:
    path: Path
    fonts_dir: Path
    has_content: bool = True


class SubtitlePreparationError(RuntimeError):
    job_error_code = "SUBTITLE_PREPARATION_FAILED"


class SubtitleDecoderError(SubtitlePreparationError):
    job_error_code = "SUBTITLE_DECODER_FAILED"


class SubtitleFontPreparationError(SubtitlePreparationError):
    job_error_code = "SUBTITLE_FONT_PREPARATION_FAILED"


class BitmapSubtitlePrerollIndeterminateError(SubtitlePreparationError):
    job_error_code = "BITMAP_SUBTITLE_PREROLL_INDETERMINATE"


async def render_clip_file(
    plan: ClipRenderPlan,
    settings: Settings,
    *,
    progress: ProgressCallback,
) -> RenderedClipFile:
    output_dir = settings.resolved_work_dir / "jobs" / plan.job_id
    output_path = output_dir / "rendered.mp4"
    await asyncio.to_thread(_prepare_output_path, output_dir, output_path)
    try:
        duration_ms = plan.source_end_ms - plan.source_start_ms
        subtitle_preroll_ms = await _subtitle_preroll_ms(plan, settings)
        prepared_subtitle = await _prepare_text_subtitle_file(
            plan,
            settings,
            output_dir,
            subtitle_preroll_ms=subtitle_preroll_ms,
        )
        argv = build_ffmpeg_clip_args(
            plan,
            settings,
            output_path,
            subtitle_preroll_ms=subtitle_preroll_ms,
            prepared_text_subtitle=prepared_subtitle,
        )
        await _run_ffmpeg_with_progress(
            argv,
            duration_ms=duration_ms,
            progress=progress,
            cwd=output_dir,
        )
        return RenderedClipFile(path=output_path, duration_ms=duration_ms)
    except BaseException:
        await asyncio.to_thread(
            _cleanup_failed_output_path,
            output_dir,
            settings.preserve_job_workdirs,
        )
        raise


def _prepare_output_path(output_dir: Path, output_path: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def _cleanup_failed_output_path(output_dir: Path, preserve_workdir: bool) -> None:
    if preserve_workdir:
        logger.warning("Preserving media job work directory after render failure: %s", output_dir)
        return
    shutil.rmtree(output_dir, ignore_errors=True)


def build_ffmpeg_clip_args(
    plan: ClipRenderPlan,
    settings: Settings,
    output_path: Path,
    *,
    subtitle_preroll_ms: int = 0,
    prepared_text_subtitle: PreparedTextSubtitle | None = None,
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
        # Input option: decode only the requested range plus any subtitle preroll.
        "-t",
        f"{input_duration_seconds:.3f}",
        "-i",
        plan.source_media.local_path,
    ]

    subtitle_filter = _subtitle_video_filter(
        plan,
        preroll_seconds,
        prepared_text_subtitle,
    )
    if subtitle_filter.complex_filter:
        argv.extend(["-filter_complex", subtitle_filter.filter_value])
    else:
        argv.extend(
            [
                "-vf",
                subtitle_filter.filter_value,
                "-af",
                _audio_filter(preroll_seconds, duration_seconds),
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
            *output_color_args(),
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
            # Output option: prevent preroll or a delayed stream from extending the clip.
            "-t",
            f"{duration_seconds:.3f}",
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
    plan: ClipRenderPlan,
    preroll_seconds: float,
    prepared_text_subtitle: PreparedTextSubtitle | None,
) -> VideoFilterPlan:
    base = build_video_base_filter(plan.hdr, plan.hdr_strategy)
    trim = (
        f"trim=start={preroll_seconds:.3f}:"
        f"duration={_duration_seconds(plan):.3f},setpts=PTS-STARTPTS"
    )
    strategy = plan.selected_subtitle.strategy
    stream = plan.selected_subtitle.stream
    if strategy == "embedded_text" and stream is not None:
        if prepared_text_subtitle is None:
            raise ValueError("Embedded text subtitles must be prepared before rendering.")
        if not prepared_text_subtitle.has_content:
            return VideoFilterPlan(
                f"{base},{trim}",
                f"0:{plan.source_media.video_streams[0].stream_index}",
            )
        source = _filtergraph_quote(_prepared_filter_path(prepared_text_subtitle.path))
        fonts_dir = _prepared_filter_path(prepared_text_subtitle.fonts_dir)
        fonts_arg = f":fontsdir={_filtergraph_quote(fonts_dir)}"
        return VideoFilterPlan(
            f"{base},subtitles=filename={source}{fonts_arg},{trim}",
            f"0:{plan.source_media.video_streams[0].stream_index}",
        )
    if strategy == "external_text" and stream is not None:
        if prepared_text_subtitle is None:
            raise ValueError("External text subtitles must be prepared before rendering.")
        if not prepared_text_subtitle.has_content:
            return VideoFilterPlan(
                f"{base},{trim}",
                f"0:{plan.source_media.video_streams[0].stream_index}",
            )
        source = _filtergraph_quote(_prepared_filter_path(prepared_text_subtitle.path))
        fonts_dir = _prepared_filter_path(prepared_text_subtitle.fonts_dir)
        fonts_arg = f":fontsdir={_filtergraph_quote(fonts_dir)}"
        return VideoFilterPlan(
            f"{base},subtitles=filename={source}{fonts_arg},{trim}",
            f"0:{plan.source_media.video_streams[0].stream_index}",
        )
    if strategy == "bitmap" and stream is not None:
        filter_value = (
            f"[0:{plan.source_media.video_streams[0].stream_index}]"
            f"{base},setpts=PTS-STARTPTS[v];"
            f"[0:{stream.stream_index}]setpts=PTS-STARTPTS[s];"
            f"[v][s]overlay,format=yuv420p,{trim}[outv];"
            f"[0:{plan.selected_audio_stream.stream_index}]"
            f"{_audio_filter(preroll_seconds, _duration_seconds(plan))}[outa]"
        )
        return VideoFilterPlan(filter_value, "[outv]", "[outa]", complex_filter=True)
    return VideoFilterPlan(
        f"{base},{trim}",
        f"0:{plan.source_media.video_streams[0].stream_index}",
    )


def _audio_filter(preroll_seconds: float, duration_seconds: float) -> str:
    return f"atrim=start={preroll_seconds:.3f}:duration={duration_seconds:.3f},asetpts=PTS-STARTPTS"


def _duration_seconds(plan: ClipRenderPlan) -> float:
    return (plan.source_end_ms - plan.source_start_ms) / 1000


def _filtergraph_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "'\\''")
    return f"'{escaped}'"


def _prepared_filter_path(path: Path) -> str:
    if path.name == "fonts":
        relative = Path("fonts")
    else:
        relative = Path("subtitles") / path.name
    return relative.as_posix()


async def _prepare_text_subtitle_file(
    plan: ClipRenderPlan,
    settings: Settings,
    work_dir: Path,
    *,
    subtitle_preroll_ms: int,
) -> PreparedTextSubtitle | None:
    strategy = plan.selected_subtitle.strategy
    stream = plan.selected_subtitle.stream
    if not plan.selected_subtitle.enabled or strategy not in {"embedded_text", "external_text"}:
        return None
    if stream is None:
        raise ValueError("Selected text subtitle stream is missing.")

    subtitles_dir = work_dir / "subtitles"
    fonts_dir = work_dir / "fonts"
    try:
        await asyncio.to_thread(subtitles_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(fonts_dir.mkdir, parents=True, exist_ok=True)
    except OSError as error:
        raise SubtitlePreparationError(
            "The selected subtitle could not be prepared in the job work directory."
        ) from error
    prepared = subtitles_dir / _prepared_subtitle_filename(stream.codec_name)

    if strategy == "external_text":
        downloaded = subtitles_dir / f"downloaded-{prepared.name}"
        await _download_external_text_subtitle(plan, settings, downloaded)
        try:
            await _extract_external_text_subtitle(
                plan,
                settings,
                downloaded,
                prepared,
                subtitle_preroll_ms=subtitle_preroll_ms,
            )
        except CommandError as error:
            raise SubtitleDecoderError(
                "FFmpeg could not align the selected external subtitle to the clip range."
            ) from error
        await asyncio.to_thread(downloaded.unlink, missing_ok=True)
    else:
        try:
            await _extract_embedded_text_subtitle(
                plan,
                settings,
                prepared,
                subtitle_preroll_ms=subtitle_preroll_ms,
            )
        except CommandError as error:
            raise SubtitleDecoderError(
                "FFmpeg could not decode the selected subtitle during preparation."
            ) from error
    try:
        await _extract_font_attachments(plan, settings, fonts_dir)
    except CommandError as error:
        raise SubtitleFontPreparationError(
            "FFmpeg could not extract embedded subtitle fonts during preparation."
        ) from error
    has_content = await asyncio.to_thread(_prepared_subtitle_has_content, prepared)
    return PreparedTextSubtitle(
        path=prepared,
        fonts_dir=fonts_dir,
        has_content=has_content,
    )


def _prepared_subtitle_has_content(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _prepared_subtitle_filename(codec_name: str | None) -> str:
    codec = (codec_name or "").casefold()
    extension = {
        "ass": "ass",
        "ssa": "ass",
        "subrip": "srt",
        "srt": "srt",
        "webvtt": "vtt",
        "mov_text": "srt",
        "text": "srt",
    }.get(codec, "ass")
    return f"selected-subtitle.{extension}"


async def _extract_embedded_text_subtitle(
    plan: ClipRenderPlan,
    settings: Settings,
    output_path: Path,
    *,
    subtitle_preroll_ms: int,
) -> None:
    stream = plan.selected_subtitle.stream
    if stream is None:
        raise ValueError("Selected text subtitle stream is missing.")
    decode_start_ms = max(0, plan.source_start_ms - subtitle_preroll_ms)
    preroll_seconds = (plan.source_start_ms - decode_start_ms) / 1000
    duration_seconds = _duration_seconds(plan) + preroll_seconds
    await run_command(
        [
            settings.ffmpeg_path,
            "-hide_banner",
            "-y",
            "-ss",
            f"{decode_start_ms / 1000:.3f}",
            "-i",
            plan.source_media.local_path,
            "-t",
            f"{duration_seconds:.3f}",
            "-map",
            f"0:{stream.stream_index}",
            "-c:s",
            _subtitle_encoder(stream.codec_name),
            os.fspath(output_path),
        ],
        timeout_seconds=settings.media_preparation_timeout_seconds,
    )


async def _extract_external_text_subtitle(
    plan: ClipRenderPlan,
    settings: Settings,
    input_path: Path,
    output_path: Path,
    *,
    subtitle_preroll_ms: int,
) -> None:
    stream = plan.selected_subtitle.stream
    if stream is None:
        raise ValueError("Selected external text subtitle stream is missing.")
    decode_start_ms = max(0, plan.source_start_ms - subtitle_preroll_ms)
    preroll_seconds = (plan.source_start_ms - decode_start_ms) / 1000
    duration_seconds = _duration_seconds(plan) + preroll_seconds
    await run_command(
        [
            settings.ffmpeg_path,
            "-hide_banner",
            "-y",
            "-i",
            os.fspath(input_path),
            "-ss",
            f"{decode_start_ms / 1000:.3f}",
            "-t",
            f"{duration_seconds:.3f}",
            "-map",
            "0:0",
            "-c:s",
            _subtitle_encoder(stream.codec_name),
            "-output_ts_offset",
            f"{-decode_start_ms / 1000:.3f}",
            os.fspath(output_path),
        ],
        timeout_seconds=settings.media_preparation_timeout_seconds,
    )


def _subtitle_encoder(codec_name: str | None) -> str:
    codec = (codec_name or "").casefold()
    if codec in {"ass", "ssa"}:
        return "ass"
    if codec == "webvtt":
        return "webvtt"
    return "srt"


class ExternalSubtitleDownloadError(RuntimeError):
    job_error_code = "EXTERNAL_SUBTITLE_DOWNLOAD_FAILED"


class ExternalSubtitleAuthenticationError(ExternalSubtitleDownloadError):
    job_error_code = "EXTERNAL_SUBTITLE_AUTH_FAILED"


async def _download_external_text_subtitle(
    plan: ClipRenderPlan,
    settings: Settings,
    output_path: Path,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    external_url = plan.selected_subtitle.external_url
    if not external_url:
        raise ExternalSubtitleDownloadError("Selected external subtitle has no download URL.")
    if not settings.plex_token:
        raise ExternalSubtitleAuthenticationError(
            "External subtitle download could not be authenticated because Plex credentials are "
            "unavailable."
        )
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    try:
        response = await client.get(
            external_url,
            headers={"X-Plex-Token": settings.plex_token},
        )
    except httpx.RequestError as error:
        raise ExternalSubtitleDownloadError(
            "The selected external subtitle could not be retrieved from Plex."
        ) from error
    finally:
        if owns_client:
            await client.aclose()
    if response.status_code in {401, 403}:
        raise ExternalSubtitleAuthenticationError(
            "Plex rejected authentication while retrieving the selected external subtitle."
        )
    if response.status_code != 200:
        raise ExternalSubtitleDownloadError(
            f"Plex returned HTTP {response.status_code} while retrieving the selected external "
            "subtitle."
        )
    await asyncio.to_thread(output_path.write_bytes, response.content)


async def _extract_font_attachments(
    plan: ClipRenderPlan, settings: Settings, fonts_dir: Path
) -> None:
    attachment_indexes = [
        attachment.stream_index
        for attachment in plan.source_media.attachment_streams
        if _is_supported_font_attachment(
            attachment.codec_name,
            attachment.filename,
            attachment.mime_type,
        )
    ]
    if not attachment_indexes:
        return
    dump_args = [
        value
        for stream_index in attachment_indexes
        for value in (f"-dump_attachment:{stream_index}", "")
    ]
    await run_command(
        [
            settings.ffmpeg_path,
            "-hide_banner",
            "-y",
            *dump_args,
            "-i",
            plan.source_media.local_path,
            # Attachments are available after input initialization. Map a single
            # video stream and emit zero frames so FFmpeg exits without traversing
            # or decoding the source.
            "-map",
            f"0:{plan.source_media.video_streams[0].stream_index}",
            "-frames:v",
            "0",
            "-f",
            "null",
            "-",
        ],
        timeout_seconds=settings.media_preparation_timeout_seconds,
        cwd=fonts_dir,
    )


def _is_supported_font_attachment(
    codec_name: str | None, filename: str | None, mime_type: str | None
) -> bool:
    codec = (codec_name or "").casefold()
    if codec in SUPPORTED_FONT_ATTACHMENT_CODECS:
        return True
    suffix = Path(filename or "").suffix.casefold()
    if suffix in SUPPORTED_FONT_ATTACHMENT_EXTENSIONS:
        return True
    normalized_mime_type = (mime_type or "").split(";", maxsplit=1)[0].strip().casefold()
    return normalized_mime_type in SUPPORTED_FONT_ATTACHMENT_MIME_TYPES


async def _subtitle_preroll_ms(plan: ClipRenderPlan, settings: Settings) -> int:
    if not plan.selected_subtitle.enabled:
        return 0
    if plan.selected_subtitle.strategy in {"embedded_text", "external_text"}:
        return min(TEXT_SUBTITLE_PREROLL_MS, plan.source_start_ms)
    if plan.selected_subtitle.strategy != "bitmap":
        return 0
    try:
        preroll_ms = await _bitmap_packet_preroll_ms(plan, settings)
    except CommandError as error:
        raise BitmapSubtitlePrerollIndeterminateError(
            "The selected bitmap subtitle has no usable packet sequence to reconstruct at clip "
            "start."
        ) from error
    return min(preroll_ms, plan.source_start_ms)


async def _bitmap_packet_preroll_ms(plan: ClipRenderPlan, settings: Settings) -> int:
    stream = plan.selected_subtitle.stream
    if stream is None:
        raise BitmapSubtitlePrerollIndeterminateError(
            "The selected bitmap subtitle stream is unavailable for preroll inspection."
        )
    window_start_ms = max(0, plan.source_start_ms - BITMAP_SUBTITLE_PROBE_WINDOW_MS)
    start = window_start_ms / 1000
    end = (plan.source_start_ms + 1_000) / 1000
    result = await run_command(
        [
            settings.ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            str(stream.stream_index),
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
        "schemaVersion": 2,
        "application": "MediaClipMakarr",
        "clipId": plan.clip_id,
        "revision": plan.revision,
        "title": plan.title,
        "library": plan.library,
        "metadata": {
            "title": plan.title,
            "custom_title": plan.custom_title,
            "automatic_title": plan.automatic_title or plan.title,
            "library": plan.library,
            "media_type": plan.media_type,
            "movie_title": plan.movie_title,
            "movie_year": plan.movie_year,
            "show_name": plan.show_name,
            "episode_title": plan.episode_title,
            "season_number": plan.season_number,
            "episode_number": plan.episode_number,
            "clip_number": plan.clip_number,
            "plex_username": plan.plex_user,
        },
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
        "videoProcessing": {
            "hdrStrategy": plan.hdr_strategy,
            "sourceHdr": {
                "hdr10": plan.hdr.hdr10,
                "hlg": plan.hdr.hlg,
                "dolbyVision": plan.hdr.dolby_vision,
                "dolbyVisionProfile": plan.hdr.dolby_vision_profile,
                "dolbyVisionBaseLayerCompatible": (
                    plan.hdr.dolby_vision_base_layer_compatible
                ),
                "dolbyVisionBlCompatibilityId": (
                    plan.hdr.dolby_vision_bl_compatibility_id
                ),
            },
            "sourceColor": plan.hdr.color.model_dump(mode="json"),
        },
    }
    checksum_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["checksum"] = hashlib.sha256(checksum_payload.encode()).hexdigest()
    return "MediaClipMakarr " + json.dumps(payload, sort_keys=True, separators=(",", ":"))


async def _run_ffmpeg_with_progress(
    argv: list[str],
    *,
    duration_ms: int,
    progress: ProgressCallback,
    cwd: Path | None = None,
) -> None:
    await progress(0.0, "Starting FFmpeg.")
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
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
