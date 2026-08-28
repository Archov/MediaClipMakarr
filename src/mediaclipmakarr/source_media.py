from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mediaclipmakarr.application_settings import EffectiveApplicationSettings
from mediaclipmakarr.config import Settings
from mediaclipmakarr.plex import PlexPartStream, PlexSession
from mediaclipmakarr.source_paths import SourcePathMapping, resolve_mapped_source_path
from mediaclipmakarr.subprocesses import (
    CommandError,
    CommandNotFoundError,
    CommandResult,
    run_command,
)

SourceMediaErrorCode = Literal[
    "PLEX_SOURCE_PART_UNAVAILABLE",
    "SOURCE_PATH_UNMAPPED",
    "SOURCE_PATH_REJECTED",
    "SOURCE_PATH_MISSING",
    "SOURCE_PROBE_UNAVAILABLE",
    "SOURCE_PROBE_FAILED",
    "SOURCE_PROBE_INVALID",
    "VIDEO_STREAM_UNAVAILABLE",
    "AUDIO_STREAM_UNAVAILABLE",
    "AUDIO_STREAM_AMBIGUOUS",
    "ADVANCED_MEDIA_NOT_SUPPORTED",
]

CommandRunner = Callable[..., Awaitable[CommandResult]]
BlockingRunner = Callable[..., Awaitable[Any]]


class SourceMediaError(Exception):
    def __init__(
        self,
        code: SourceMediaErrorCode,
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


class SourceFingerprint(BaseModel):
    size_bytes: int
    modified_at: datetime


class VideoColorMetadata(BaseModel):
    color_space: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_range: str | None = None


class MediaStreamIdentity(BaseModel):
    stream_index: int
    codec_type: str
    codec_name: str | None = None
    language: str | None = None
    title: str | None = None


class VideoStreamIdentity(MediaStreamIdentity):
    width: int | None = None
    height: int | None = None
    color: VideoColorMetadata


class ResolvedSourceMedia(BaseModel):
    plex_path: str
    local_path: str
    fingerprint: SourceFingerprint
    duration_ms: int | None
    video_streams: list[VideoStreamIdentity]
    audio_streams: list[MediaStreamIdentity]
    subtitle_streams: list[MediaStreamIdentity]
    selected_audio_stream: MediaStreamIdentity
    subtitles_forced_off: bool = True


class FFProbeStream(BaseModel):
    model_config = ConfigDict(extra="allow")

    index: int
    codec_type: str
    codec_name: str | None = None
    width: int | None = None
    height: int | None = None
    color_space: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_range: str | None = None
    tags: dict[str, Any] = Field(default_factory=dict)
    side_data_list: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("tags", mode="before")
    @classmethod
    def default_tags(cls, value: object) -> object:
        return value or {}

    @field_validator("side_data_list", mode="before")
    @classmethod
    def default_side_data_list(cls, value: object) -> object:
        return value or []


class FFProbeFormat(BaseModel):
    model_config = ConfigDict(extra="allow")

    duration: str | None = None


class FFProbePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    streams: list[FFProbeStream]
    format: FFProbeFormat = Field(default_factory=FFProbeFormat)


@dataclass(frozen=True, slots=True)
class SourceFileSnapshot:
    path: Path
    fingerprint: SourceFingerprint


def _resolve_existing_source_file(
    plex_path: str,
    mappings: list[SourcePathMapping],
    approved_source_roots: list[Path],
) -> SourceFileSnapshot:
    try:
        path = resolve_mapped_source_path(plex_path, mappings, approved_source_roots)
        stat = path.stat()
    except ValueError as error:
        message = str(error)
        if "No configured" in message:
            raise SourceMediaError("SOURCE_PATH_UNMAPPED", message, retryable=True) from error
        raise SourceMediaError("SOURCE_PATH_REJECTED", message) from error
    except FileNotFoundError as error:
        raise SourceMediaError(
            "SOURCE_PATH_MISSING",
            "The mapped Plex media file does not exist on the configured source mount.",
            retryable=True,
        ) from error
    except OSError as error:
        raise SourceMediaError(
            "SOURCE_PATH_REJECTED",
            "The mapped Plex media path could not be inspected safely.",
        ) from error

    if not path.is_file():
        raise SourceMediaError(
            "SOURCE_PATH_REJECTED",
            "The mapped Plex media path is not a regular file.",
        )

    return SourceFileSnapshot(
        path=path,
        fingerprint=SourceFingerprint(
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
        ),
    )


async def resolve_and_probe_source_media(
    session: PlexSession,
    effective_settings: EffectiveApplicationSettings,
    bootstrap_settings: Settings,
    *,
    run_blocking: BlockingRunner,
    runner: CommandRunner = run_command,
) -> ResolvedSourceMedia:
    if not session.plex_part_file:
        raise SourceMediaError(
            "PLEX_SOURCE_PART_UNAVAILABLE",
            "Plex did not report a file path for the active media part.",
            retryable=True,
        )

    source_file = await run_blocking(
        _resolve_existing_source_file,
        session.plex_part_file,
        effective_settings.source_path_mappings,
        bootstrap_settings.resolved_source_dirs,
    )
    probe = await _probe_source(source_file.path, bootstrap_settings, runner=runner)
    _reject_advanced_media(probe)
    video_streams = _video_streams(probe)
    if not video_streams:
        raise SourceMediaError(
            "VIDEO_STREAM_UNAVAILABLE",
            "The selected source media does not contain a usable video stream.",
        )
    selected_audio = _select_audio_stream(probe, session.selected_audio_streams)

    return ResolvedSourceMedia(
        plex_path=session.plex_part_file,
        local_path=str(source_file.path),
        fingerprint=source_file.fingerprint,
        duration_ms=_duration_ms(probe),
        video_streams=[
            VideoStreamIdentity(
                stream_index=stream.index,
                codec_type=stream.codec_type,
                codec_name=stream.codec_name,
                language=_stream_language(stream),
                title=_stream_title(stream),
                width=stream.width,
                height=stream.height,
                color=VideoColorMetadata(
                    color_space=stream.color_space,
                    color_transfer=stream.color_transfer,
                    color_primaries=stream.color_primaries,
                    color_range=stream.color_range,
                ),
            )
            for stream in video_streams
        ],
        audio_streams=[_stream_identity(stream) for stream in _audio_streams(probe)],
        subtitle_streams=[
            _stream_identity(stream) for stream in probe.streams if stream.codec_type == "subtitle"
        ],
        selected_audio_stream=selected_audio,
    )


async def _probe_source(
    path: Path,
    settings: Settings,
    *,
    runner: CommandRunner,
) -> FFProbePayload:
    try:
        result = await runner(
            [
                settings.ffprobe_path,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                path,
            ],
            timeout_seconds=settings.subprocess_timeout_seconds,
        )
    except CommandNotFoundError as error:
        raise SourceMediaError(
            "SOURCE_PROBE_UNAVAILABLE",
            "ffprobe is not available to inspect the selected source media.",
            retryable=True,
        ) from error
    except CommandError as error:
        raise SourceMediaError(
            "SOURCE_PROBE_FAILED",
            "ffprobe could not inspect the selected source media.",
            retryable=True,
        ) from error

    try:
        payload = json.loads(result.stdout)
        return FFProbePayload.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as error:
        raise SourceMediaError(
            "SOURCE_PROBE_INVALID",
            "ffprobe returned source media metadata in an unexpected format.",
        ) from error


def _duration_ms(probe: FFProbePayload) -> int | None:
    if probe.format.duration is None:
        return None
    try:
        duration_seconds = float(probe.format.duration)
    except ValueError:
        return None
    return max(0, round(duration_seconds * 1000))


def _stream_identity(stream: FFProbeStream) -> MediaStreamIdentity:
    return MediaStreamIdentity(
        stream_index=stream.index,
        codec_type=stream.codec_type,
        codec_name=stream.codec_name,
        language=_stream_language(stream),
        title=_stream_title(stream),
    )


def _stream_language(stream: FFProbeStream) -> str | None:
    value = stream.tags.get("language")
    return str(value) if value else None


def _stream_title(stream: FFProbeStream) -> str | None:
    value = stream.tags.get("title")
    return str(value) if value else None


def _audio_streams(probe: FFProbePayload) -> list[FFProbeStream]:
    return [stream for stream in probe.streams if stream.codec_type == "audio"]


def _video_streams(probe: FFProbePayload) -> list[FFProbeStream]:
    return [stream for stream in probe.streams if stream.codec_type == "video"]


def _select_audio_stream(
    probe: FFProbePayload, selected_audio_streams: Sequence[PlexPartStream]
) -> MediaStreamIdentity:
    audio_streams = _audio_streams(probe)
    if not selected_audio_streams:
        raise SourceMediaError(
            "AUDIO_STREAM_UNAVAILABLE",
            "Plex did not report a selected audio stream for the active media part.",
            retryable=True,
        )
    if len(selected_audio_streams) > 1:
        raise SourceMediaError(
            "AUDIO_STREAM_AMBIGUOUS",
            "Plex reported multiple selected audio streams for the active media part.",
            retryable=True,
        )

    selected = selected_audio_streams[0]
    if selected.stream_index is not None:
        matches = [stream for stream in audio_streams if stream.index == selected.stream_index]
        if not matches:
            raise SourceMediaError(
                "AUDIO_STREAM_UNAVAILABLE",
                "The Plex-selected audio stream is not present in the probed source file.",
                retryable=True,
            )
        return _stream_identity(matches[0])

    if len(audio_streams) == 1:
        return _stream_identity(audio_streams[0])

    raise SourceMediaError(
        "AUDIO_STREAM_AMBIGUOUS",
        "The Plex-selected audio stream could not be mapped unambiguously to the source file.",
        retryable=True,
    )


def _reject_advanced_media(probe: FFProbePayload) -> None:
    detected: list[str] = []
    for stream in probe.streams:
        if stream.codec_type != "video":
            continue
        transfer = (stream.color_transfer or "").casefold()
        if transfer == "smpte2084":
            detected.append("HDR10/PQ")
        if transfer == "arib-std-b67":
            detected.append("HLG")
        if _has_dolby_vision_metadata(stream):
            detected.append("Dolby Vision")

    if detected:
        unique = sorted(set(detected))
        raise SourceMediaError(
            "ADVANCED_MEDIA_NOT_SUPPORTED",
            (
                "Phase 1 only supports SDR sources. Detected advanced media: "
                f"{', '.join(unique)}."
            ),
        )


def _has_dolby_vision_metadata(stream: FFProbeStream) -> bool:
    values: list[str] = []
    values.extend(str(value) for value in stream.tags.values())
    for item in stream.side_data_list:
        values.extend(str(value) for value in item.values())
    text = " ".join(values).casefold()
    return "dolby vision" in text or "dovi" in text
