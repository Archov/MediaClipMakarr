from __future__ import annotations

import httpx
import pytest

from mediaclipmakarr.application_settings import EffectiveApplicationSettings
from mediaclipmakarr.plex import PlexSessionPoller, parse_video_sessions
from mediaclipmakarr.plex import test_plex_connection as check_plex_connection


def effective_settings() -> EffectiveApplicationSettings:
    return EffectiveApplicationSettings(
        plex_url="http://plex.example:32400",
        plex_token="valid-token",
        source_path_mappings=[],
        timezone="UTC",
        timezone_configured=True,
        x264_preset="veryfast",
        environment_managed={
            "plex_url": False,
            "plex_token": False,
            "source_path_mappings": False,
            "timezone": False,
            "x264_preset": False,
        },
    )


@pytest.mark.asyncio
async def test_valid_plex_identity_passes_connection_test() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Plex-Token"] == "valid-token"
        if request.url.path == "/identity":
            return httpx.Response(
                200,
                content=(
                    b'<MediaContainer friendlyName="Living Room Plex" version="1.2.3" '
                    b'machineIdentifier="abc" />'
                ),
            )
        assert request.url.path == "/library/sections"
        return httpx.Response(200, content=b'<MediaContainer size="0" />')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await check_plex_connection(
            "http://plex.example:32400", "valid-token", client=client
        )

    assert result.connected is True
    assert result.code == "PLEX_CONNECTED"
    assert result.server_name == "Living Room Plex"


@pytest.mark.asyncio
async def test_invalid_url_and_token_have_distinct_connection_failures() -> None:
    invalid_url = await check_plex_connection("not-a-url", "token")

    def reject_token(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/identity":
            return httpx.Response(200, content=b'<MediaContainer version="1.2.3" />')
        assert request.url.path == "/library/sections"
        return httpx.Response(401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject_token)) as client:
        invalid_token = await check_plex_connection(
            "http://plex.example:32400", "wrong-token", client=client
        )

    assert invalid_url.code == "PLEX_INVALID_URL"
    assert invalid_token.code == "PLEX_INVALID_TOKEN"
    assert invalid_url.code != invalid_token.code


@pytest.mark.asyncio
async def test_unreachable_server_is_not_reported_as_bad_credentials() -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unreachable)) as client:
        result = await check_plex_connection(
            "http://missing.example:32400", "token", client=client
        )

    assert result.connected is False
    assert result.code == "PLEX_UNREACHABLE"


def test_video_session_identity_is_separate_from_media_identity() -> None:
    first_payload = b"""
    <MediaContainer size="2">
      <Video type="episode" ratingKey="101" key="/library/metadata/101"
        grandparentTitle="Example Show" title="Same Title" viewOffset="1000" duration="30000">
        <User id="1" title="Alice" />
        <Player title="Living Room" machineIdentifier="player-a" state="playing" />
        <Session id="session-a" />
        <Media id="media-101" key="/library/parts/101"><Part id="part-101" /></Media>
      </Video>
      <Video type="episode" ratingKey="101" key="/library/metadata/101"
        grandparentTitle="Example Show" title="Same Title" viewOffset="4000" duration="30000">
        <User id="2" title="Bob" />
        <Player title="Bedroom" machineIdentifier="player-b" state="paused" />
        <Session id="session-b" />
        <Media id="media-101" key="/library/parts/101"><Part id="part-101" /></Media>
      </Video>
    </MediaContainer>
    """
    changed_media_payload = b"""
    <MediaContainer size="1">
      <Video type="episode" ratingKey="202" key="/library/metadata/202"
        grandparentTitle="Example Show" title="Next Title" viewOffset="0" duration="30000">
        <User id="1" title="Alice" />
        <Player title="Living Room" machineIdentifier="player-a" state="playing" />
        <Session id="session-a" />
        <Media id="media-202" key="/library/parts/202"><Part id="part-202" /></Media>
      </Video>
    </MediaContainer>
    """

    first = parse_video_sessions(first_payload)
    changed = parse_video_sessions(changed_media_payload)

    assert len({session.session_identity for session in first}) == 2
    assert first[0].title == "Example Show - Same Title"
    assert first[0].media_identity == first[1].media_identity
    assert first[0].session_identity == changed[0].session_identity
    assert first[0].media_identity != changed[0].media_identity


@pytest.mark.asyncio
async def test_session_poller_reports_disappearance_without_persisting_sessions() -> None:
    payloads = [
        b"""
        <MediaContainer size="1">
          <Video type="movie" ratingKey="501" title="A Movie" viewOffset="12000" duration="90000">
            <User id="1" title="Alice" />
            <Player title="Living Room" machineIdentifier="player-a" state="playing" />
            <Session id="session-a" />
            <Media id="media-501"><Part id="part-501" /></Media>
          </Video>
        </MediaContainer>
        """,
        b'<MediaContainer size="0" />',
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/status/sessions"
        assert request.headers["X-Plex-Token"] == "valid-token"
        return httpx.Response(200, content=payloads.pop(0))

    async def load_settings() -> EffectiveApplicationSettings:
        return effective_settings()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        poller = PlexSessionPoller(load_settings, client=client)
        first = await poller.poll_once()
        second = await poller.poll_once()

    assert first.status == "ok"
    assert [session.title for session in first.sessions] == ["A Movie"]
    assert second.status == "ok"
    assert second.sessions == []
