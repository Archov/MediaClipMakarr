from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from mediaclipmakarr.immich import (
    ImmichAssetNotFoundError,
    ImmichAuthError,
    ImmichInvalidResponseError,
    set_immich_asset_description,
    upload_immich_asset_sync,
)
from mediaclipmakarr.immich import test_immich_connection as check_immich_connection


@pytest.mark.asyncio
async def test_valid_immich_credentials_pass_connection_test() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/server/ping":
            return httpx.Response(200, json={"res": "pong"})
        assert request.headers["x-api-key"] == "valid-key"
        if request.url.path == "/api/api-keys/me":
            return httpx.Response(
                200,
                json={
                    "id": "key-1",
                    "name": "mediaclipmakarr",
                    "permissions": ["asset.upload", "tag.read"],
                    "createdAt": "2026-01-01T00:00:00.000Z",
                    "updatedAt": "2026-01-01T00:00:00.000Z",
                },
            )
        assert request.url.path == "/api/server/version"
        return httpx.Response(200, json={"major": 1, "minor": 118, "patch": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await check_immich_connection(
            "http://immich.example:2283", "valid-key", client=client
        )

    assert result.connected is True
    assert result.code == "IMMICH_CONNECTED"
    assert result.server_version == "1.118.0"
    assert result.api_key_permissions == ["asset.upload", "tag.read"]


@pytest.mark.asyncio
async def test_not_configured_when_url_or_key_missing() -> None:
    missing_key = await check_immich_connection("http://immich.example:2283", None)
    missing_url = await check_immich_connection(None, "some-key")

    assert missing_key.code == "IMMICH_NOT_CONFIGURED"
    assert missing_url.code == "IMMICH_NOT_CONFIGURED"
    assert missing_key.connected is False


@pytest.mark.asyncio
async def test_invalid_url_and_api_key_have_distinct_connection_failures() -> None:
    invalid_url = await check_immich_connection("not-a-url", "key")

    def reject_key(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/server/ping":
            return httpx.Response(200, json={"res": "pong"})
        assert request.url.path == "/api/api-keys/me"
        return httpx.Response(401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject_key)) as client:
        invalid_key = await check_immich_connection(
            "http://immich.example:2283", "wrong-key", client=client
        )

    assert invalid_url.code == "IMMICH_INVALID_URL"
    assert invalid_key.code == "IMMICH_INVALID_API_KEY"
    assert invalid_url.code != invalid_key.code


@pytest.mark.asyncio
async def test_unreachable_server_is_not_reported_as_bad_credentials() -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unreachable)) as client:
        result = await check_immich_connection(
            "http://missing.example:2283", "key", client=client
        )

    assert result.connected is False
    assert result.code == "IMMICH_UNREACHABLE"


@pytest.mark.asyncio
async def test_key_scoped_to_only_the_documented_permissions_still_validates() -> None:
    # A key created with exactly the scopes from the "Required permissions" dialog (no
    # user.read) must still pass validation — the old /users/me check required user.read
    # and rejected these otherwise-valid, minimally-scoped keys.
    granted = [
        "asset.upload",
        "asset.update",
        "asset.read",
        "tag.read",
        "tag.create",
        "tag.asset",
        "album.read",
        "album.create",
        "albumAsset.create",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/server/ping":
            return httpx.Response(200, json={"res": "pong"})
        if request.url.path == "/api/api-keys/me":
            return httpx.Response(
                200,
                json={
                    "id": "key-1",
                    "name": "mediaclipmakarr",
                    "permissions": granted,
                    "createdAt": "2026-01-01T00:00:00.000Z",
                    "updatedAt": "2026-01-01T00:00:00.000Z",
                },
            )
        assert request.url.path == "/api/server/version"
        return httpx.Response(200, json={"major": 1, "minor": 118, "patch": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await check_immich_connection(
            "http://immich.example:2283", "scoped-key", client=client
        )

    assert result.connected is True
    assert result.code == "IMMICH_CONNECTED"
    assert result.api_key_permissions == granted


@pytest.mark.asyncio
async def test_unsupported_api_response_is_distinguished_from_http_error() -> None:
    def bad_ping_payload(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/server/ping"
        return httpx.Response(200, json={"unexpected": "shape"})

    def http_error(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/server/ping"
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(bad_ping_payload)) as client:
        invalid_response = await check_immich_connection(
            "http://immich.example:2283", "key", client=client
        )
    async with httpx.AsyncClient(transport=httpx.MockTransport(http_error)) as client:
        server_error = await check_immich_connection(
            "http://immich.example:2283", "key", client=client
        )

    assert invalid_response.code == "IMMICH_INVALID_RESPONSE"
    assert server_error.code == "IMMICH_HTTP_ERROR"
    assert invalid_response.code != server_error.code


def test_upload_immich_asset_returns_asset_id_on_success(tmp_path) -> None:
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"fake mp4 bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/assets"
        assert request.headers["x-api-key"] == "valid-key"
        return httpx.Response(201, json={"id": "asset-123", "status": "created"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        asset_id = upload_immich_asset_sync(
            clip_path,
            "http://immich.example:2283",
            "valid-key",
            file_created_at=datetime(2026, 1, 1, tzinfo=UTC),
            file_modified_at=datetime(2026, 1, 2, tzinfo=UTC),
            client=client,
        )

    assert asset_id == "asset-123"


def test_upload_immich_asset_treats_duplicate_status_as_success(tmp_path) -> None:
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"fake mp4 bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "existing-asset", "status": "duplicate"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        asset_id = upload_immich_asset_sync(
            clip_path,
            "http://immich.example:2283",
            "valid-key",
            file_created_at=datetime(2026, 1, 1, tzinfo=UTC),
            file_modified_at=datetime(2026, 1, 2, tzinfo=UTC),
            client=client,
        )

    assert asset_id == "existing-asset"


def test_upload_immich_asset_raises_invalid_response_on_missing_id(tmp_path) -> None:
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"fake mp4 bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"status": "created"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ImmichInvalidResponseError):
            upload_immich_asset_sync(
                clip_path,
                "http://immich.example:2283",
                "valid-key",
                file_created_at=datetime(2026, 1, 1, tzinfo=UTC),
                file_modified_at=datetime(2026, 1, 2, tzinfo=UTC),
                client=client,
            )


def test_upload_immich_asset_raises_auth_error_on_401(tmp_path) -> None:
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"fake mp4 bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ImmichAuthError):
            upload_immich_asset_sync(
                clip_path,
                "http://immich.example:2283",
                "bad-key",
                file_created_at=datetime(2026, 1, 1, tzinfo=UTC),
                file_modified_at=datetime(2026, 1, 2, tzinfo=UTC),
                client=client,
            )


@pytest.mark.asyncio
async def test_set_immich_asset_description_succeeds() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/assets/asset-123"
        assert request.method == "PATCH"
        return httpx.Response(200, json={"id": "asset-123", "description": "My clip"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await set_immich_asset_description(
            "asset-123", "My clip", "http://immich.example:2283", "valid-key", client=client
        )


@pytest.mark.asyncio
async def test_set_immich_asset_description_raises_not_found_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ImmichAssetNotFoundError):
            await set_immich_asset_description(
                "missing-asset",
                "My clip",
                "http://immich.example:2283",
                "valid-key",
                client=client,
            )


@pytest.mark.asyncio
async def test_set_immich_asset_description_raises_not_found_on_400() -> None:
    # Confirmed against a live server: this deprecated endpoint reports a
    # missing/invalid asset id as HTTP 400, not 404.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ImmichAssetNotFoundError):
            await set_immich_asset_description(
                "missing-asset",
                "My clip",
                "http://immich.example:2283",
                "valid-key",
                client=client,
            )
