from __future__ import annotations

import asyncio
import contextlib

import httpx
import pytest

import mediaclipmakarr.plex as plex_module
from mediaclipmakarr.application_settings import EffectiveApplicationSettings
from mediaclipmakarr.plex import PlexSessionPoller, parse_library_names, parse_video_sessions
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


def test_library_names_preserve_plex_display_case() -> None:
    names = parse_library_names(
        b'<MediaContainer><Directory title="Movies" /><Directory title="Anime" />'
        b'<Directory title="anime" /><Directory title="" /></MediaContainer>'
    )

    assert names == ["Movies", "Anime"]


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
        result = await check_plex_connection("http://missing.example:32400", "token", client=client)

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


def test_video_session_uses_selected_media_part_and_audio_stream() -> None:
    payload = b"""
    <MediaContainer size="1">
      <Video type="movie" ratingKey="501" title="A Movie" viewOffset="12000" duration="90000">
        <User id="1" title="Alice" />
        <Player title="Living Room" machineIdentifier="player-a" state="playing" />
        <Session id="session-a" />
        <Media id="media-501-a">
          <Part id="part-501-a" key="/library/parts/501-a" file="/plex/wrong.mkv">
            <Stream id="stream-audio-wrong" streamType="2" index="1" selected="1" />
          </Part>
        </Media>
        <Media id="media-501-b" selected="1">
          <Part id="part-501-b1" key="/library/parts/501-b1" file="/plex/wrong-part.mkv" />
          <Part id="part-501-b2" key="/library/parts/501-b2" file="/plex/right.mkv" selected="1">
            <Stream id="stream-video" streamType="1" index="0" />
            <Stream id="stream-audio" streamType="2" index="2" codec="aac"
              languageCode="eng" title="Stereo" selected="1" />
            <Stream id="stream-subtitle-external" streamType="3" index="-1" codec="srt"
              languageCode="eng" title="External English" key="/library/streams/501.srt"
              selected="1" />
          </Part>
        </Media>
      </Video>
    </MediaContainer>
    """

    [session] = parse_video_sessions(payload)

    assert session.plex_media_key == "media-501-b"
    assert session.plex_part_id == "part-501-b2"
    assert session.plex_part_key == "/library/parts/501-b2"
    assert session.plex_part_file == "/plex/right.mkv"
    assert len(session.selected_audio_streams) == 1
    assert session.selected_audio_streams[0].stream_index == 2
    assert session.selected_audio_streams[0].language == "eng"
    assert session.selected_subtitle_streams[0].key == "/library/streams/501.srt"
    assert session.subtitle_streams[0].codec == "srt"


def test_video_session_preserves_plex_hdr_and_dolby_vision_metadata() -> None:
    payload = b"""
    <MediaContainer size="1">
      <Video ratingKey="501" title="Movie" type="movie" duration="90000">
        <User id="1" title="Alice" />
        <Player machineIdentifier="player-1" state="playing" />
        <Session id="session-1" />
        <Media id="media-1" selected="1" videoDynamicRange="HDR">
          <Part id="part-1" file="/plex/Movie.mkv" selected="1">
            <Stream streamType="1" index="0" colorTrc="smpte2084"
                    colorPrimaries="bt2020" colorSpace="bt2020nc"
                    DOVIProfile="8" DOVIBLCompatID="1" />
            <Stream streamType="2" index="1" selected="1" />
          </Part>
        </Media>
      </Video>
    </MediaContainer>
    """

    [parsed] = parse_video_sessions(payload)

    assert parsed.video_metadata.dynamic_range == "HDR"
    assert parsed.video_metadata.color_transfer == "smpte2084"
    assert parsed.video_metadata.dolby_vision_profile == 8
    assert parsed.video_metadata.dolby_vision_bl_compatibility_id == 1


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


@pytest.mark.asyncio
async def test_session_poller_converts_settings_loader_failure_to_error_snapshot(
    monkeypatch,
) -> None:
    async def fail_settings_load() -> EffectiveApplicationSettings:
        raise RuntimeError("settings cache unavailable")

    logged_messages: list[str] = []
    monkeypatch.setattr(
        plex_module.logger,
        "exception",
        lambda message, *args, **kwargs: logged_messages.append(message),
    )
    poller = PlexSessionPoller(fail_settings_load)
    snapshot = await poller.poll_once()

    assert snapshot.status == "error"
    assert snapshot.sessions == []
    assert poller.version == 1
    assert logged_messages == ["Unexpected error while polling Plex sessions."]


@pytest.mark.asyncio
async def test_session_poller_converts_unexpected_plex_payload_failure_to_error_snapshot(
    monkeypatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/status/sessions"
        return httpx.Response(
            200,
            content=b"""
            <MediaContainer size="1">
              <Video type="movie" ratingKey="501" title="A Movie"
                viewOffset="inf" duration="90000">
                <User id="1" title="Alice" />
                <Player title="Living Room" machineIdentifier="player-a" state="playing" />
                <Session id="session-a" />
                <Media id="media-501"><Part id="part-501" /></Media>
              </Video>
            </MediaContainer>
            """,
        )

    async def load_settings() -> EffectiveApplicationSettings:
        return effective_settings()

    logged_messages: list[str] = []
    monkeypatch.setattr(
        plex_module.logger,
        "exception",
        lambda message, *args, **kwargs: logged_messages.append(message),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        poller = PlexSessionPoller(load_settings, client=client)
        snapshot = await poller.poll_once()

    assert snapshot.status == "error"
    assert snapshot.sessions == []
    assert poller.version == 1
    assert logged_messages == ["Unexpected error while polling Plex sessions."]


@pytest.mark.asyncio
async def test_session_poller_run_loop_continues_after_escaped_poll_error(monkeypatch) -> None:
    class EscapingPoller(PlexSessionPoller):
        calls = 0

        async def poll_once(self) -> object:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("escaped poll failure")
            return await super().poll_once()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/status/sessions"
        return httpx.Response(200, content=b'<MediaContainer size="0" />')

    async def load_settings() -> EffectiveApplicationSettings:
        return effective_settings()

    logged_messages: list[str] = []
    monkeypatch.setattr(
        plex_module.logger,
        "exception",
        lambda message, *args, **kwargs: logged_messages.append(message),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        poller = EscapingPoller(load_settings, interval_seconds=0.01, client=client)
        task = asyncio.create_task(poller._run())
        first_snapshot, version, changed = await poller.wait_for_change(0, timeout_seconds=1.0)
        second_snapshot, _version, changed_again = await poller.wait_for_change(
            version, timeout_seconds=1.0
        )
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert changed is True
    assert first_snapshot.status == "error"
    assert changed_again is True
    assert second_snapshot.status == "ok"
    assert poller.version >= 2
    assert logged_messages == ["Unexpected error escaped the Plex session poller."]
