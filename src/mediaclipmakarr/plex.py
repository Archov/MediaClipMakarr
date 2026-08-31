from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal
from xml.etree import ElementTree

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from mediaclipmakarr.application_settings import EffectiveApplicationSettings, normalize_plex_url
from mediaclipmakarr.hdr import PlexVideoMetadata

logger = logging.getLogger(__name__)

PlexSessionSnapshotStatus = Literal[
    "ok",
    "not_configured",
    "invalid_url",
    "invalid_token",
    "http_error",
    "invalid_response",
    "unreachable",
    "error",
]


class PlexConnectionResult(BaseModel):
    connected: bool
    code: str
    message: str
    server_name: str | None = None
    server_version: str | None = None


class PlexConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plex_url: str | None = None
    plex_token: SecretStr | None = None

    @model_validator(mode="after")
    def require_complete_candidate(self) -> PlexConnectionRequest:
        if (self.plex_url is None) != (self.plex_token is None):
            raise ValueError("Provide both a Plex URL and token, or neither.")
        return self


class PlexPartStream(BaseModel):
    id: str | None = None
    key: str | None = None
    stream_index: int | None = None
    stream_type: int | None = None
    codec: str | None = None
    language: str | None = None
    title: str | None = None
    selected: bool = False


class PlexSession(BaseModel):
    session_identity: str
    media_identity: str
    title: str
    media_type: str
    plex_user: str | None
    player: str | None
    state: str
    position_ms: int
    duration_ms: int | None
    sampled_at: datetime
    plex_rating_key: str | None = None
    plex_media_key: str | None = None
    plex_part_id: str | None = None
    plex_part_key: str | None = None
    plex_part_file: str | None = Field(default=None, exclude=True)
    video_metadata: PlexVideoMetadata = Field(default_factory=PlexVideoMetadata, exclude=True)
    selected_audio_streams: list[PlexPartStream] = Field(default_factory=list)
    selected_subtitle_streams: list[PlexPartStream] = Field(default_factory=list)
    subtitle_streams: list[PlexPartStream] = Field(default_factory=list)


class PlexSessionSnapshot(BaseModel):
    status: PlexSessionSnapshotStatus
    message: str
    sampled_at: datetime
    sessions: list[PlexSession]


class PlexSessionError(Exception):
    def __init__(self, status: PlexSessionSnapshotStatus, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


SettingsLoader = Callable[[], Awaitable[EffectiveApplicationSettings]]


def utc_now() -> datetime:
    return datetime.now(UTC)


def _local_name(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    for child in element:
        if _local_name(child) == name:
            return child
    return None


def _children(element: ElementTree.Element | None, name: str) -> list[ElementTree.Element]:
    if element is None:
        return []
    return [child for child in element if _local_name(child) == name]


def _is_selected(element: ElementTree.Element) -> bool:
    return element.attrib.get("selected", "").strip().casefold() in {"1", "true", "yes"}


def _active_child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    children = _children(element, name)
    selected = next((child for child in children if _is_selected(child)), None)
    if selected is not None:
        return selected
    return children[0] if children else None


def _int_attribute(element: ElementTree.Element, name: str) -> int | None:
    value = element.attrib.get(name)
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _identity(prefix: str, parts: list[str | None]) -> str:
    normalized = [part.strip() for part in parts if part and part.strip()]
    digest = hashlib.sha256("\x1f".join(normalized).encode("utf-8")).hexdigest()[:24]
    return f"plex-{prefix}:{digest}"


def _display_title(video: ElementTree.Element) -> str:
    media_type = video.attrib.get("type", "video")
    if media_type == "episode":
        show = video.attrib.get("grandparentTitle")
        title = video.attrib.get("title")
        if show and title:
            return f"{show} - {title}"
    return video.attrib.get("title") or video.attrib.get("grandparentTitle") or "Untitled video"


def _parse_part_stream(stream: ElementTree.Element) -> PlexPartStream:
    return PlexPartStream(
        id=stream.attrib.get("id"),
        key=stream.attrib.get("key"),
        stream_index=_int_attribute(stream, "index"),
        stream_type=_int_attribute(stream, "streamType"),
        codec=stream.attrib.get("codec"),
        language=stream.attrib.get("languageCode") or stream.attrib.get("language"),
        title=stream.attrib.get("title"),
        selected=_is_selected(stream),
    )


def _attribute(element: ElementTree.Element | None, *names: str) -> str | None:
    if element is None:
        return None
    expected = {name.casefold() for name in names}
    return next(
        (value for key, value in element.attrib.items() if key.casefold() in expected and value),
        None,
    )


def _int_value(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_video_metadata(
    media: ElementTree.Element | None,
    video_stream: ElementTree.Element | None,
) -> PlexVideoMetadata:
    return PlexVideoMetadata(
        dynamic_range=_attribute(media, "videoDynamicRange", "dynamicRange"),
        color_space=_attribute(video_stream, "colorSpace", "matrixCoefficients"),
        color_transfer=_attribute(video_stream, "colorTrc", "colorTransfer", "transfer"),
        color_primaries=_attribute(video_stream, "colorPrimaries", "primaries"),
        color_range=_attribute(video_stream, "colorRange", "range"),
        dolby_vision_profile=_int_value(
            _attribute(video_stream, "DOVIProfile", "dolbyVisionProfile", "dvProfile")
        ),
        dolby_vision_bl_compatibility_id=_int_value(
            _attribute(
                video_stream,
                "DOVIBLCompatID",
                "dolbyVisionBLCompatibilityID",
                "dvBLSignalCompatibilityID",
            )
        ),
    )


def parse_video_sessions(
    payload: bytes, *, sampled_at: datetime | None = None
) -> list[PlexSession]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise PlexSessionError(
            "invalid_response", "Plex did not return valid session XML."
        ) from error
    if _local_name(root) != "MediaContainer":
        raise PlexSessionError("invalid_response", "Plex did not return a session container.")

    sample_time = sampled_at or utc_now()
    sessions: list[PlexSession] = []
    for video in root:
        if _local_name(video) != "Video":
            continue

        player = _child(video, "Player")
        user = _child(video, "User")
        session = _child(video, "Session")
        media = _active_child(video, "Media")
        part = _active_child(media, "Part") if media is not None else None

        user_id = user.attrib.get("id") if user is not None else None
        username = user.attrib.get("title") if user is not None else None
        player_machine = player.attrib.get("machineIdentifier") if player is not None else None
        player_title = player.attrib.get("title") if player is not None else None
        player_product = player.attrib.get("product") if player is not None else None
        session_id = session.attrib.get("id") if session is not None else None
        rating_key = video.attrib.get("ratingKey") or video.attrib.get("key")
        guid = video.attrib.get("guid")
        media_id = media.attrib.get("id") if media is not None else None
        media_key = media.attrib.get("key") if media is not None else None
        part_id = part.attrib.get("id") if part is not None else None
        part_key = part.attrib.get("key") if part is not None else None
        part_file = part.attrib.get("file") if part is not None else None
        part_stream_elements = _children(part, "Stream")
        part_streams = [_parse_part_stream(stream) for stream in part_stream_elements]
        video_stream = next(
            (
                stream
                for stream in part_stream_elements
                if _int_attribute(stream, "streamType") == 1
            ),
            None,
        )
        selected_audio_streams = [
            parsed for parsed in part_streams if parsed.stream_type == 2 and parsed.selected
        ]
        subtitle_streams = [parsed for parsed in part_streams if parsed.stream_type == 3]
        selected_subtitle_streams = [parsed for parsed in subtitle_streams if parsed.selected]

        sessions.append(
            PlexSession(
                session_identity=_identity(
                    "session",
                    [user_id or username, player_machine or player_title, session_id],
                ),
                media_identity=_identity(
                    "media",
                    [rating_key, guid, media_id or media_key, part_id or part_key],
                ),
                title=_display_title(video),
                media_type=video.attrib.get("type", "video"),
                plex_user=username,
                player=player_title or player_product,
                state=(player.attrib.get("state") if player is not None else None) or "unknown",
                position_ms=_int_attribute(video, "viewOffset") or 0,
                duration_ms=_int_attribute(video, "duration"),
                sampled_at=sample_time,
                plex_rating_key=rating_key,
                plex_media_key=media_key or media_id,
                plex_part_id=part_id,
                plex_part_key=part_key,
                plex_part_file=part_file,
                video_metadata=_parse_video_metadata(media, video_stream),
                selected_audio_streams=selected_audio_streams,
                selected_subtitle_streams=selected_subtitle_streams,
                subtitle_streams=subtitle_streams,
            )
        )
    return sessions


class PlexClient:
    def __init__(self, plex_url: str, plex_token: str, *, client: httpx.AsyncClient):
        self.plex_url = normalize_plex_url(plex_url)
        self.plex_token = plex_token
        self.client = client

    async def fetch_video_sessions(self) -> list[PlexSession]:
        sampled_at = utc_now()
        try:
            response = await self.client.get(
                f"{self.plex_url}/status/sessions",
                headers={"Accept": "application/xml", "X-Plex-Token": self.plex_token},
            )
        except (httpx.InvalidURL, httpx.UnsupportedProtocol) as error:
            raise PlexSessionError(
                "invalid_url", "The configured Plex URL could not be used for a request."
            ) from error
        except httpx.RequestError as error:
            raise PlexSessionError(
                "unreachable", "The Plex server could not be reached at the configured URL."
            ) from error

        if response.status_code in {401, 403}:
            raise PlexSessionError("invalid_token", "Plex rejected the configured token.")
        if response.status_code != 200:
            raise PlexSessionError(
                "http_error",
                f"Plex returned HTTP {response.status_code} while loading active sessions.",
            )
        return parse_video_sessions(response.content, sampled_at=sampled_at)


class PlexSessionPoller:
    def __init__(
        self,
        settings_loader: SettingsLoader,
        *,
        interval_seconds: float = 1.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.settings_loader = settings_loader
        self.interval_seconds = interval_seconds
        self._client = client
        self._owns_client = client is None
        self._task: asyncio.Task[None] | None = None
        self._condition = asyncio.Condition()
        self._version = 0
        self._snapshot = PlexSessionSnapshot(
            status="not_configured",
            message="Configure Plex to discover active video sessions.",
            sampled_at=utc_now(),
            sessions=[],
        )

    @property
    def snapshot(self) -> PlexSessionSnapshot:
        return self._snapshot

    @property
    def version(self) -> int:
        return self._version

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._task = asyncio.create_task(self._run(), name="plex-session-poller")
        await self.poll_once()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def poll_once(self) -> PlexSessionSnapshot:
        try:
            settings = await self.settings_loader()
            if not settings.plex_url or not settings.plex_token:
                snapshot = PlexSessionSnapshot(
                    status="not_configured",
                    message="Configure Plex to discover active video sessions.",
                    sampled_at=utc_now(),
                    sessions=[],
                )
            else:
                if self._client is None:
                    self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
                client = PlexClient(settings.plex_url, settings.plex_token, client=self._client)
                sessions = await client.fetch_video_sessions()
                snapshot = PlexSessionSnapshot(
                    status="ok",
                    message=(
                        "No active Plex video sessions were found."
                        if not sessions
                        else "Active Plex video sessions loaded."
                    ),
                    sampled_at=utc_now(),
                    sessions=sessions,
                )
        except ValueError:
            snapshot = PlexSessionSnapshot(
                status="invalid_url",
                message="The configured Plex URL is not a valid HTTP or HTTPS server URL.",
                sampled_at=utc_now(),
                sessions=[],
            )
        except PlexSessionError as error:
            snapshot = PlexSessionSnapshot(
                status=error.status,
                message=error.message,
                sampled_at=utc_now(),
                sessions=[],
            )
        except Exception:
            logger.exception("Unexpected error while polling Plex sessions.")
            snapshot = self._unexpected_error_snapshot()

        await self._publish_snapshot(snapshot)
        return snapshot

    async def _publish_snapshot(self, snapshot: PlexSessionSnapshot) -> None:
        async with self._condition:
            self._snapshot = snapshot
            self._version += 1
            self._condition.notify_all()

    def _unexpected_error_snapshot(self) -> PlexSessionSnapshot:
        return PlexSessionSnapshot(
            status="error",
            message="Plex session polling failed unexpectedly. Check the application logs.",
            sampled_at=utc_now(),
            sessions=[],
        )

    async def wait_for_change(
        self, version: int, *, timeout_seconds: float | None = None
    ) -> tuple[PlexSessionSnapshot, int, bool]:
        async with self._condition:
            if self._version == version:
                try:
                    await asyncio.wait_for(
                        self._condition.wait_for(lambda: self._version != version),
                        timeout_seconds,
                    )
                except TimeoutError:
                    return self._snapshot, self._version, False
            return self._snapshot, self._version, True

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected error escaped the Plex session poller.")
                await self._publish_snapshot(self._unexpected_error_snapshot())


def snapshot_sse_payload(snapshot: PlexSessionSnapshot) -> str:
    data = snapshot.model_dump(mode="json")
    return f"event: snapshot\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def test_plex_connection(
    plex_url: str,
    plex_token: str | None,
    *,
    client: httpx.AsyncClient | None = None,
) -> PlexConnectionResult:
    if not plex_url or not plex_token:
        return PlexConnectionResult(
            connected=False,
            code="PLEX_NOT_CONFIGURED",
            message="Configure both the Plex server URL and token before testing.",
        )

    try:
        normalized_url = normalize_plex_url(plex_url)
    except ValueError:
        return PlexConnectionResult(
            connected=False,
            code="PLEX_INVALID_URL",
            message="The configured Plex URL is not a valid HTTP or HTTPS server URL.",
        )

    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        identity_response = await active_client.get(
            f"{normalized_url}/identity",
            headers={"Accept": "application/xml", "X-Plex-Token": plex_token},
        )
        if identity_response.status_code in {401, 403}:
            return PlexConnectionResult(
                connected=False,
                code="PLEX_INVALID_TOKEN",
                message="Plex rejected the configured token.",
            )
        if identity_response.status_code != 200:
            return PlexConnectionResult(
                connected=False,
                code="PLEX_HTTP_ERROR",
                message=(
                    f"Plex returned HTTP {identity_response.status_code} while testing the "
                    "connection."
                ),
            )

        try:
            identity = ElementTree.fromstring(identity_response.content)
        except ElementTree.ParseError:
            return PlexConnectionResult(
                connected=False,
                code="PLEX_INVALID_RESPONSE",
                message="The configured server did not return a valid Plex identity response.",
            )
        if identity.tag.rsplit("}", 1)[-1] != "MediaContainer":
            return PlexConnectionResult(
                connected=False,
                code="PLEX_INVALID_RESPONSE",
                message="The configured server did not return a Plex identity response.",
            )

        authenticated_response = await active_client.get(
            f"{normalized_url}/library/sections",
            headers={"Accept": "application/xml", "X-Plex-Token": plex_token},
        )
        if authenticated_response.status_code in {401, 403}:
            return PlexConnectionResult(
                connected=False,
                code="PLEX_INVALID_TOKEN",
                message="Plex rejected the configured token.",
            )
        if authenticated_response.status_code != 200:
            return PlexConnectionResult(
                connected=False,
                code="PLEX_HTTP_ERROR",
                message=(
                    f"Plex returned HTTP {authenticated_response.status_code} while validating "
                    "the token."
                ),
            )
        try:
            authenticated_payload = ElementTree.fromstring(authenticated_response.content)
        except ElementTree.ParseError:
            return PlexConnectionResult(
                connected=False,
                code="PLEX_INVALID_RESPONSE",
                message="Plex did not return a valid authenticated API response.",
            )
        if authenticated_payload.tag.rsplit("}", 1)[-1] != "MediaContainer":
            return PlexConnectionResult(
                connected=False,
                code="PLEX_INVALID_RESPONSE",
                message="Plex did not return an authenticated API response.",
            )
    except (httpx.InvalidURL, httpx.UnsupportedProtocol):
        return PlexConnectionResult(
            connected=False,
            code="PLEX_INVALID_URL",
            message="The configured Plex URL could not be used for a request.",
        )
    except httpx.RequestError:
        return PlexConnectionResult(
            connected=False,
            code="PLEX_UNREACHABLE",
            message="The Plex server could not be reached at the configured URL.",
        )
    finally:
        if owns_client:
            await active_client.aclose()

    return PlexConnectionResult(
        connected=True,
        code="PLEX_CONNECTED",
        message="Connected to Plex successfully.",
        server_name=identity.attrib.get("friendlyName"),
        server_version=identity.attrib.get("version"),
    )
