from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from mediaclipmakarr.immich import (
    ImmichAssetNotFoundError,
    ImmichAuthError,
    ImmichInvalidResponseError,
    ImmichUnreachableError,
    set_immich_asset_description,
    tag_immich_assets,
    untag_immich_assets,
    upload_immich_asset_sync,
    upsert_immich_tags,
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
        return httpx.Response(201, json={"id": "asset-123", "duplicate": False})

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


def test_upload_immich_asset_sends_timestamps_in_the_configured_local_timezone(tmp_path) -> None:
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"fake mp4 bytes")

    sent: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        # Immich shows a video's "Details" date as given, with no EXIF/GPS to
        # derive a local offset from — so it must be sent in the timezone the
        # clip was actually experienced in, the same way a photo app shows a
        # vacation photo's timestamp in the timezone it was taken, not UTC.
        body = request.content.decode("utf-8", errors="ignore")
        for field in ("fileCreatedAt", "fileModifiedAt"):
            start = body.index(f'name="{field}"')
            value_start = body.index("\r\n\r\n", start) + 4
            value_end = body.index("\r\n", value_start)
            sent[field] = body[value_start:value_end]
        return httpx.Response(201, json={"id": "asset-123", "duplicate": False})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        upload_immich_asset_sync(
            clip_path,
            "http://immich.example:2283",
            "valid-key",
            file_created_at=datetime(2026, 1, 1, 6, tzinfo=UTC),
            file_modified_at=datetime(2026, 1, 2, 6, tzinfo=UTC),
            local_timezone="US/Central",
            client=client,
        )

    # 06:00 UTC on Jan 1 is 00:00 CST (UTC-6, no DST in January) — same instant,
    # expressed with the local offset instead of a bare "+00:00".
    assert sent["fileCreatedAt"].startswith("2026-01-01T00:00:00")
    assert sent["fileCreatedAt"].endswith("-06:00")
    assert sent["fileModifiedAt"].startswith("2026-01-02T00:00:00")
    assert sent["fileModifiedAt"].endswith("-06:00")


def test_upload_immich_asset_treats_duplicate_response_as_success(tmp_path) -> None:
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"fake mp4 bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        # The documented response shape for a duplicate upload (id + duplicate
        # bool) — only `id` matters to us, so this must still succeed even
        # though it carries no `status` field.
        return httpx.Response(200, json={"id": "existing-asset", "duplicate": True})

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
        return httpx.Response(201, json={"duplicate": False})

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


@pytest.mark.asyncio
async def test_upsert_immich_tags_returns_path_to_id_map() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/api/tags"
        payload = json.loads(request.read())
        assert payload == {"tags": ["TV Shows", "TV Shows/Breaking Bad"]}
        return httpx.Response(
            200,
            json=[
                {
                    "id": "tag-library",
                    "name": "TV Shows",
                    "value": "TV Shows",
                    "createdAt": "2026-01-01T00:00:00.000Z",
                    "updatedAt": "2026-01-01T00:00:00.000Z",
                },
                {
                    "id": "tag-show",
                    "name": "Breaking Bad",
                    "value": "TV Shows/Breaking Bad",
                    "parentId": "tag-library",
                    "createdAt": "2026-01-01T00:00:00.000Z",
                    "updatedAt": "2026-01-01T00:00:00.000Z",
                },
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await upsert_immich_tags(
            ["TV Shows", "TV Shows/Breaking Bad"],
            "http://immich.example:2283",
            "valid-key",
            client=client,
        )

    assert result == {"TV Shows": "tag-library", "TV Shows/Breaking Bad": "tag-show"}


@pytest.mark.asyncio
async def test_upsert_immich_tags_raises_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ImmichAuthError):
            await upsert_immich_tags(
                ["Tag"], "http://immich.example:2283", "bad-key", client=client
            )


@pytest.mark.asyncio
async def test_upsert_immich_tags_raises_unreachable_on_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ImmichUnreachableError):
            await upsert_immich_tags(
                ["Tag"], "http://missing.example:2283", "key", client=client
            )


@pytest.mark.asyncio
async def test_upsert_immich_tags_raises_invalid_response_on_malformed_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not": "a list"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ImmichInvalidResponseError):
            await upsert_immich_tags(
                ["Tag"], "http://immich.example:2283", "valid-key", client=client
            )


@pytest.mark.asyncio
async def test_tag_immich_assets_succeeds() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/api/tags/assets"
        return httpx.Response(200, json=[{"id": "asset-123", "success": True}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await tag_immich_assets(
            "asset-123",
            ["tag-1", "tag-2"],
            "http://immich.example:2283",
            "valid-key",
            client=client,
        )


@pytest.mark.asyncio
async def test_tag_immich_assets_raises_invalid_response_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ImmichInvalidResponseError):
            await tag_immich_assets(
                "asset-123", ["tag-1"], "http://immich.example:2283", "valid-key", client=client
            )


@pytest.mark.asyncio
async def test_tag_immich_assets_treats_a_duplicate_result_as_success() -> None:
    # HTTP 200 with a per-asset "already tagged" result must not be reported as
    # a failure — it's the desired end state, just not newly achieved this call.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[{"id": "asset-123", "success": False, "error": "duplicate"}]
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await tag_immich_assets(
            "asset-123", ["tag-1"], "http://immich.example:2283", "valid-key", client=client
        )


@pytest.mark.asyncio
async def test_tag_immich_assets_raises_on_a_per_asset_failure_despite_http_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[{"id": "asset-123", "success": False, "error": "no_permission"}]
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ImmichInvalidResponseError):
            await tag_immich_assets(
                "asset-123", ["tag-1"], "http://immich.example:2283", "valid-key", client=client
            )


@pytest.mark.asyncio
async def test_tag_immich_assets_accepts_a_count_summary_response() -> None:
    # Confirmed against a live Immich 3.1.0 server: PUT /api/tags/assets returns
    # a plain {"count": N} summary, not the per-asset array — no per-asset detail
    # is available to check, so any well-formed count response is a success.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await tag_immich_assets(
            "asset-123", ["tag-1"], "http://immich.example:2283", "valid-key", client=client
        )


@pytest.mark.asyncio
async def test_tag_immich_assets_raises_when_the_asset_is_missing_from_the_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": "some-other-asset", "success": True}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ImmichInvalidResponseError):
            await tag_immich_assets(
                "asset-123", ["tag-1"], "http://immich.example:2283", "valid-key", client=client
            )


@pytest.mark.asyncio
async def test_untag_immich_assets_succeeds() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/api/tags/tag-1/assets"
        assert json.loads(request.read()) == {"ids": ["asset-123"]}
        return httpx.Response(200, json=[{"id": "asset-123", "success": True}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await untag_immich_assets(
            "tag-1", ["asset-123"], "http://immich.example:2283", "valid-key", client=client
        )


@pytest.mark.asyncio
async def test_untag_immich_assets_raises_invalid_response_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ImmichInvalidResponseError):
            await untag_immich_assets(
                "tag-1", ["asset-123"], "http://immich.example:2283", "valid-key", client=client
            )


@pytest.mark.asyncio
async def test_untag_immich_assets_treats_not_found_as_success() -> None:
    # The asset already didn't have this tag — the goal (tag not applied) is
    # already achieved, so this must not be reported as a failure.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[{"id": "asset-123", "success": False, "error": "not_found"}]
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await untag_immich_assets(
            "tag-1", ["asset-123"], "http://immich.example:2283", "valid-key", client=client
        )


@pytest.mark.asyncio
async def test_untag_immich_assets_accepts_a_count_summary_response() -> None:
    # Confirmed against a live Immich 3.1.0 server: DELETE /api/tags/{id}/assets
    # also returns a plain {"count": N} summary, not the per-asset array.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await untag_immich_assets(
            "tag-1", ["asset-123"], "http://immich.example:2283", "valid-key", client=client
        )


@pytest.mark.asyncio
async def test_untag_immich_assets_raises_on_a_per_asset_failure_despite_http_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[{"id": "asset-123", "success": False, "error": "no_permission"}]
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ImmichInvalidResponseError):
            await untag_immich_assets(
                "tag-1", ["asset-123"], "http://immich.example:2283", "valid-key", client=client
            )
