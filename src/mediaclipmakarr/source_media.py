from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mediaclipmakarr.application_settings import EffectiveApplicationSettings
from mediaclipmakarr.config import Settings
from mediaclipmakarr.hdr import HdrCapabilities, VideoColorMetadata, classify_hdr
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
    "SUBTITLE_STREAM_UNAVAILABLE",
    "SUBTITLE_STREAM_AMBIGUOUS",
    "SUBTITLE_STREAM_UNSUPPORTED",
    "EXTERNAL_SUBTITLE_STREAM_UNAVAILABLE",
    "EXTERNAL_SUBTITLE_URL_UNAVAILABLE",
    "EXTERNAL_SUBTITLE_URL_INVALID",
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
        alternatives: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.alternatives = alternatives or []


class SourceFingerprint(BaseModel):
    size_bytes: int
    modified_at: datetime


class MediaStreamIdentity(BaseModel):
    stream_index: int
    codec_type: str
    codec_name: str | None = None
    language: str | None = None
    title: str | None = None
    filename: str | None = None
    mime_type: str | None = None


TrackKind = Literal["video", "audio", "subtitle", "attachment"]
SubtitleKind = Literal["text", "bitmap", "unsupported"]
SubtitleStrategy = Literal["off", "embedded_text", "external_text", "bitmap"]


class TrackDescriptor(BaseModel):
    kind: TrackKind
    stream_index: int | None
    plex_track_id: str | None = None
    plex_key: str | None = None
    codec: str | None = None
    language: str | None = None
    title: str | None = None
    selected: bool = False
    available: bool = True
    unavailable_reason: str | None = None
    subtitle_kind: SubtitleKind | None = None
    external: bool = False


class MediaCapabilities(BaseModel):
    duration_ms: int | None
    video_tracks: list[TrackDescriptor]
    audio_tracks: list[TrackDescriptor]
    subtitle_tracks: list[TrackDescriptor]
    attachment_tracks: list[TrackDescriptor]
    default_audio_stream_index: int
    default_subtitle_stream_index: int | None = None
    subtitles_forced_off: bool = True
    hdr: HdrCapabilities
    warnings: list[str] = Field(default_factory=list)


class SubtitleSelection(BaseModel):
    enabled: bool = False
    stream: MediaStreamIdentity | None = None
    strategy: SubtitleStrategy = "off"
    external_url: str | None = None


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
    attachment_streams: list[MediaStreamIdentity] = Field(default_factory=list)
    capabilities: MediaCapabilities | None = None
    selected_audio_stream: MediaStreamIdentity
    selected_subtitle: SubtitleSelection = Field(default_factory=SubtitleSelection)
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
    requested_audio_stream_index: int | None = None,
    requested_subtitle_stream_index: int | None = None,
    subtitles_enabled: bool = False,
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
    video_streams = _video_streams(probe)
    if not video_streams:
        raise SourceMediaError(
            "VIDEO_STREAM_UNAVAILABLE",
            "The selected source media does not contain a usable video stream.",
        )
    selected_audio = _select_audio_stream(
        probe,
        session.selected_audio_streams,
        requested_stream_index=requested_audio_stream_index,
    )
    selected_subtitle = _select_subtitle_stream(
        probe,
        session.subtitle_streams or session.selected_subtitle_streams,
        session.selected_subtitle_streams,
        requested_stream_index=requested_subtitle_stream_index,
        subtitles_enabled=subtitles_enabled,
        plex_url=effective_settings.plex_url,
    )
    capabilities = _media_capabilities(
        probe,
        session,
        selected_audio_stream=selected_audio,
        selected_subtitle=selected_subtitle,
    )

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
        attachment_streams=[
            _stream_identity(stream)
            for stream in probe.streams
            if stream.codec_type == "attachment"
        ],
        capabilities=capabilities,
        selected_audio_stream=selected_audio,
        selected_subtitle=selected_subtitle,
        subtitles_forced_off=not selected_subtitle.enabled,
    )


async def resolve_media_capabilities(
    session: PlexSession,
    effective_settings: EffectiveApplicationSettings,
    bootstrap_settings: Settings,
    *,
    run_blocking: BlockingRunner,
    runner: CommandRunner = run_command,
) -> ResolvedSourceMedia:
    return await resolve_and_probe_source_media(
        session,
        effective_settings,
        bootstrap_settings,
        run_blocking=run_blocking,
        runner=runner,
        subtitles_enabled=False,
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
        filename=_attachment_filename(stream),
        mime_type=_attachment_mime_type(stream),
    )


def _track_descriptor(
    stream: FFProbeStream,
    *,
    kind: TrackKind,
    selected: bool,
    plex_stream: PlexPartStream | None = None,
) -> TrackDescriptor:
    subtitle_kind = _subtitle_kind(stream.codec_name) if kind == "subtitle" else None
    supported = subtitle_kind != "unsupported" if kind == "subtitle" else True
    return TrackDescriptor(
        kind=kind,
        stream_index=stream.index,
        plex_track_id=plex_stream.id if plex_stream else None,
        plex_key=plex_stream.key if plex_stream else None,
        codec=stream.codec_name,
        language=_stream_language(stream),
        title=_stream_title(stream),
        selected=selected,
        available=supported,
        unavailable_reason=None if supported else "This subtitle codec cannot be burned yet.",
        subtitle_kind=subtitle_kind,
        external=bool(plex_stream and plex_stream.key and stream.index < 0),
    )


def _stream_language(stream: FFProbeStream) -> str | None:
    return _stream_tag(stream, "language")


def _stream_title(stream: FFProbeStream) -> str | None:
    return _stream_tag(stream, "title")


def _attachment_filename(stream: FFProbeStream) -> str | None:
    return _stream_tag(stream, "filename")


def _attachment_mime_type(stream: FFProbeStream) -> str | None:
    return _stream_tag(stream, "mimetype", "mime_type", "content_type")


def _stream_tag(stream: FFProbeStream, *names: str) -> str | None:
    expected = {name.casefold() for name in names}
    for name, value in stream.tags.items():
        if name.casefold() in expected and value:
            return str(value)
    return None


def _audio_streams(probe: FFProbePayload) -> list[FFProbeStream]:
    return [stream for stream in probe.streams if stream.codec_type == "audio"]


def _video_streams(probe: FFProbePayload) -> list[FFProbeStream]:
    return [stream for stream in probe.streams if stream.codec_type == "video"]


def _select_audio_stream(
    probe: FFProbePayload,
    selected_audio_streams: Sequence[PlexPartStream],
    *,
    requested_stream_index: int | None,
) -> MediaStreamIdentity:
    audio_streams = _audio_streams(probe)
    if requested_stream_index is not None:
        matches = [stream for stream in audio_streams if stream.index == requested_stream_index]
        if not matches:
            raise SourceMediaError(
                "AUDIO_STREAM_UNAVAILABLE",
                "The requested audio stream is not present in the probed source file.",
                retryable=True,
                alternatives=_alternative_tracks(audio_streams),
            )
        return _stream_identity(matches[0])

    if not selected_audio_streams:
        raise SourceMediaError(
            "AUDIO_STREAM_UNAVAILABLE",
            "Plex did not report a selected audio stream for the active media part.",
            retryable=True,
            alternatives=_alternative_tracks(audio_streams),
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
                alternatives=_alternative_tracks(audio_streams),
            )
        return _stream_identity(matches[0])

    if len(audio_streams) == 1:
        return _stream_identity(audio_streams[0])

    raise SourceMediaError(
        "AUDIO_STREAM_AMBIGUOUS",
        "The Plex-selected audio stream could not be mapped unambiguously to the source file.",
        retryable=True,
        alternatives=_alternative_tracks(audio_streams),
    )


def _select_subtitle_stream(
    probe: FFProbePayload,
    plex_subtitle_streams: Sequence[PlexPartStream],
    selected_subtitle_streams: Sequence[PlexPartStream],
    *,
    requested_stream_index: int | None,
    subtitles_enabled: bool,
    plex_url: str,
) -> SubtitleSelection:
    subtitle_streams = [stream for stream in probe.streams if stream.codec_type == "subtitle"]
    external_streams = _external_text_subtitle_streams(probe, plex_subtitle_streams)
    if not subtitles_enabled:
        return SubtitleSelection(enabled=False, strategy="off")

    selected: FFProbeStream | None = None
    if requested_stream_index is not None:
        selected = next(
            (stream for stream in subtitle_streams if stream.index == requested_stream_index),
            None,
        )
        if selected is None:
            external = next(
                (
                    stream
                    for stream in external_streams
                    if stream.stream_index == requested_stream_index
                ),
                None,
            )
            if external is not None:
                return _external_subtitle_selection(external, plex_url)
            raise SourceMediaError(
                "SUBTITLE_STREAM_UNAVAILABLE",
                "The requested subtitle stream is not present in the probed source file.",
                retryable=True,
                alternatives=_alternative_tracks(subtitle_streams),
            )
    elif not selected_subtitle_streams:
        return SubtitleSelection(enabled=False, strategy="off")
    elif len(selected_subtitle_streams) > 1:
        raise SourceMediaError(
            "SUBTITLE_STREAM_AMBIGUOUS",
            "Plex reported multiple selected subtitle streams for the active media part.",
            retryable=True,
            alternatives=_alternative_tracks(subtitle_streams),
        )
    else:
        plex_selected = selected_subtitle_streams[0]
        if plex_selected.stream_index is not None:
            selected = next(
                (
                    stream
                    for stream in subtitle_streams
                    if stream.index == plex_selected.stream_index
                ),
                None,
            )
        if selected is None:
            external = next(
                (stream for stream in external_streams if _same_plex_stream(stream, plex_selected)),
                None,
            )
            if external is not None:
                return _external_subtitle_selection(external, plex_url)
            raise SourceMediaError(
                "SUBTITLE_STREAM_UNAVAILABLE",
                "The Plex-selected subtitle stream is not present in the probed source file.",
                retryable=True,
                alternatives=_alternative_tracks(subtitle_streams),
            )

    kind = _subtitle_kind(selected.codec_name)
    if kind == "unsupported":
        raise SourceMediaError(
            "SUBTITLE_STREAM_UNSUPPORTED",
            "The selected subtitle stream uses a codec MediaClipMakarr cannot burn yet.",
            alternatives=_alternative_tracks(subtitle_streams),
        )
    strategy: SubtitleStrategy = "bitmap" if kind == "bitmap" else "embedded_text"
    return SubtitleSelection(
        enabled=True,
        stream=_stream_identity(selected),
        strategy=strategy,
    )


def _external_text_subtitle_streams(
    probe: FFProbePayload, plex_subtitle_streams: Sequence[PlexPartStream]
) -> list[PlexPartStream]:
    embedded_indexes = {stream.index for stream in probe.streams if stream.codec_type == "subtitle"}
    candidates = [
        stream
        for stream in plex_subtitle_streams
        if stream.key
        and (stream.stream_index is None or stream.stream_index not in embedded_indexes)
        and _subtitle_kind(stream.codec) == "text"
    ]
    normalized: list[PlexPartStream] = []
    used_indexes = set(embedded_indexes)
    next_virtual_index = -1
    for stream in candidates:
        stream_index = stream.stream_index
        # Plex-managed sidecars can have a download key without an FFmpeg stream index.
        # Give those tracks a request-only identity; rendering still uses the Plex key.
        if stream_index is None or stream_index in used_indexes:
            while next_virtual_index in used_indexes:
                next_virtual_index -= 1
            stream_index = next_virtual_index
            next_virtual_index -= 1
        used_indexes.add(stream_index)
        normalized.append(stream.model_copy(update={"stream_index": stream_index}))
    return normalized


def _external_subtitle_selection(stream: PlexPartStream, plex_url: str) -> SubtitleSelection:
    if stream.stream_index is None:
        raise SourceMediaError(
            "EXTERNAL_SUBTITLE_STREAM_UNAVAILABLE",
            "Plex did not provide a selectable stream index for the external subtitle.",
            retryable=True,
        )
    if not stream.key:
        raise SourceMediaError(
            "EXTERNAL_SUBTITLE_URL_UNAVAILABLE",
            "Plex did not provide a download path for the selected external subtitle.",
            retryable=True,
        )
    return SubtitleSelection(
        enabled=True,
        stream=MediaStreamIdentity(
            stream_index=stream.stream_index,
            codec_type="subtitle",
            codec_name=stream.codec,
            language=stream.language,
            title=stream.title,
        ),
        strategy="external_text",
        external_url=_external_subtitle_url(plex_url, stream.key),
    )


def _external_subtitle_url(plex_url: str, key: str) -> str:
    parsed = urlsplit(key)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        raise SourceMediaError(
            "EXTERNAL_SUBTITLE_URL_INVALID",
            "Plex returned an invalid download path for the selected external subtitle.",
        )
    if not plex_url:
        raise SourceMediaError(
            "EXTERNAL_SUBTITLE_URL_UNAVAILABLE",
            "The Plex server URL is unavailable for the selected external subtitle.",
            retryable=True,
        )
    return f"{plex_url}{key}"


def _same_plex_stream(left: PlexPartStream, right: PlexPartStream) -> bool:
    return (
        (left.id is not None and left.id == right.id)
        or (left.key is not None and left.key == right.key)
        or (left.stream_index is not None and left.stream_index == right.stream_index)
    )


def _media_capabilities(
    probe: FFProbePayload,
    session: PlexSession,
    *,
    selected_audio_stream: MediaStreamIdentity,
    selected_subtitle: SubtitleSelection,
) -> MediaCapabilities:
    video = _video_streams(probe)
    audio = _audio_streams(probe)
    subtitles = [stream for stream in probe.streams if stream.codec_type == "subtitle"]
    external_subtitles = _external_text_subtitle_streams(probe, session.subtitle_streams)
    attachments = [stream for stream in probe.streams if stream.codec_type == "attachment"]
    selected_subtitle_index = (
        selected_subtitle.stream.stream_index
        if selected_subtitle.stream is not None
        else _default_plex_stream_index(session.selected_subtitle_streams, external_subtitles)
    )
    first_video = video[0] if video else None
    subtitle_tracks = [
        _track_descriptor(
            stream,
            kind="subtitle",
            selected=stream.index == selected_subtitle_index,
            plex_stream=_matching_plex_stream(stream, session.selected_subtitle_streams),
        )
        for stream in subtitles
    ] + [
        _external_subtitle_track_descriptor(
            stream,
            selected=stream.stream_index == selected_subtitle_index,
        )
        for stream in external_subtitles
    ]
    return MediaCapabilities(
        duration_ms=_duration_ms(probe),
        video_tracks=[
            _track_descriptor(stream, kind="video", selected=index == 0)
            for index, stream in enumerate(video)
        ],
        audio_tracks=[
            _track_descriptor(
                stream,
                kind="audio",
                selected=stream.index == selected_audio_stream.stream_index,
                plex_stream=_matching_plex_stream(stream, session.selected_audio_streams),
            )
            for stream in audio
        ],
        subtitle_tracks=subtitle_tracks,
        attachment_tracks=[
            _track_descriptor(stream, kind="attachment", selected=False) for stream in attachments
        ],
        default_audio_stream_index=selected_audio_stream.stream_index,
        default_subtitle_stream_index=selected_subtitle_index,
        subtitles_forced_off=selected_subtitle_index is None,
        hdr=classify_hdr(first_video, session.video_metadata),
        warnings=[
            track.unavailable_reason
            for track in subtitle_tracks
            if track.unavailable_reason is not None
        ],
    )


def _matching_plex_stream(
    stream: FFProbeStream, plex_streams: Sequence[PlexPartStream]
) -> PlexPartStream | None:
    return next(
        (candidate for candidate in plex_streams if candidate.stream_index == stream.index),
        None,
    )


def _external_subtitle_track_descriptor(
    stream: PlexPartStream, *, selected: bool
) -> TrackDescriptor:
    available = stream.stream_index is not None and bool(stream.key)
    reason = None
    if stream.stream_index is None:
        reason = "Plex did not provide a selectable stream index for this external subtitle."
    elif not stream.key:
        reason = "Plex did not provide a download path for this external subtitle."
    return TrackDescriptor(
        kind="subtitle",
        stream_index=stream.stream_index,
        plex_track_id=stream.id,
        plex_key=stream.key,
        codec=stream.codec,
        language=stream.language,
        title=stream.title,
        selected=selected,
        available=available,
        unavailable_reason=reason,
        subtitle_kind="text",
        external=True,
    )


def _default_plex_stream_index(
    plex_streams: Sequence[PlexPartStream],
    external_streams: Sequence[PlexPartStream],
) -> int | None:
    if len(plex_streams) != 1:
        return None
    selected = plex_streams[0]
    external = next(
        (stream for stream in external_streams if _same_plex_stream(stream, selected)),
        None,
    )
    return external.stream_index if external is not None else selected.stream_index


def _alternative_tracks(streams: Sequence[FFProbeStream]) -> list[dict[str, Any]]:
    return [
        _stream_identity(stream).model_dump(mode="json")
        for stream in streams
        if stream.codec_type != "subtitle" or _subtitle_kind(stream.codec_name) != "unsupported"
    ]


def _subtitle_kind(codec_name: str | None) -> SubtitleKind:
    codec = (codec_name or "").casefold()
    if codec in {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}:
        return "text"
    if codec in {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}:
        return "bitmap"
    return "unsupported"
