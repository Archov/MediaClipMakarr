from __future__ import annotations

import httpx
import pytest

from mediaclipmakarr.plex import test_plex_connection as check_plex_connection


@pytest.mark.asyncio
async def test_valid_plex_identity_passes_connection_test() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/identity"
        assert request.headers["X-Plex-Token"] == "valid-token"
        return httpx.Response(
            200,
            content=(
                b'<MediaContainer friendlyName="Living Room Plex" version="1.2.3" '
                b'machineIdentifier="abc" />'
            ),
        )

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

    def reject_token(_request: httpx.Request) -> httpx.Response:
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
