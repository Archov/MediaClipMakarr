from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

from mediaclipmakarr.application_settings import normalize_immich_url

logger = logging.getLogger(__name__)

ImmichConnectionCode = Literal[
    "IMMICH_NOT_CONFIGURED",
    "IMMICH_INVALID_URL",
    "IMMICH_INVALID_API_KEY",
    "IMMICH_HTTP_ERROR",
    "IMMICH_INVALID_RESPONSE",
    "IMMICH_UNREACHABLE",
    "IMMICH_CONNECTED",
]

# Generous timeout for uploading a clip: connect/pool stay tight, but read/write allow
# several minutes for a large file over a slow link.
IMMICH_UPLOAD_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=600.0, pool=10.0)


class ImmichApiError(RuntimeError):
    """Base for a failed Immich API call made outside the connection test."""

    job_error_code = "IMMICH_API_ERROR"
    job_retryable = True


class ImmichAuthError(ImmichApiError):
    """The configured API key was rejected. Retrying with the same key won't help."""

    job_error_code = "IMMICH_AUTH_FAILED"
    job_retryable = False


class ImmichUnreachableError(ImmichApiError):
    """The Immich server could not be reached."""

    job_error_code = "IMMICH_UNREACHABLE"
    job_retryable = True


class ImmichAssetNotFoundError(ImmichApiError):
    """The referenced asset no longer exists on the server (HTTP 404)."""

    job_error_code = "IMMICH_ASSET_NOT_FOUND"
    job_retryable = False


class ImmichInvalidResponseError(ImmichApiError):
    """Immich returned an unexpected HTTP status or a malformed response body."""

    job_error_code = "IMMICH_INVALID_RESPONSE"
    job_retryable = True


class ImmichConnectionResult(BaseModel):
    connected: bool
    code: str
    message: str
    server_version: str | None = None
    api_key_permissions: list[str] | None = None


class ImmichConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    immich_url: str | None = None
    immich_api_key: SecretStr | None = None

    @model_validator(mode="after")
    def require_complete_candidate(self) -> ImmichConnectionRequest:
        if (self.immich_url is None) != (self.immich_api_key is None):
            raise ValueError("Provide both an Immich URL and API key, or neither.")
        return self


async def test_immich_connection(
    immich_url: str | None,
    immich_api_key: str | None,
    *,
    client: httpx.AsyncClient | None = None,
) -> ImmichConnectionResult:
    if not immich_url or not immich_api_key:
        return ImmichConnectionResult(
            connected=False,
            code="IMMICH_NOT_CONFIGURED",
            message="Configure both the Immich server URL and API key before testing.",
        )

    try:
        normalized_url = normalize_immich_url(immich_url)
    except ValueError:
        return ImmichConnectionResult(
            connected=False,
            code="IMMICH_INVALID_URL",
            message="The configured Immich URL is not a valid HTTP or HTTPS server URL.",
        )

    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        ping_response = await active_client.get(f"{normalized_url}/api/server/ping")
        if ping_response.status_code != 200:
            return ImmichConnectionResult(
                connected=False,
                code="IMMICH_HTTP_ERROR",
                message=(
                    f"Immich returned HTTP {ping_response.status_code} while testing connectivity."
                ),
            )
        try:
            ping_payload = ping_response.json()
        except ValueError:
            ping_payload = None
        if not isinstance(ping_payload, dict) or ping_payload.get("res") != "pong":
            return ImmichConnectionResult(
                connected=False,
                code="IMMICH_INVALID_RESPONSE",
                message="The configured server did not return a valid Immich ping response.",
            )

        # /api-keys/me reports on the calling key itself and requires no permission
        # beyond authentication (unlike /users/me, which needs the separate "user.read"
        # scope) — it's the right endpoint both to validate the key and to see exactly
        # which permissions it was granted.
        auth_response = await active_client.get(
            f"{normalized_url}/api/api-keys/me",
            headers={"Accept": "application/json", "x-api-key": immich_api_key},
        )
        if auth_response.status_code in {401, 403}:
            return ImmichConnectionResult(
                connected=False,
                code="IMMICH_INVALID_API_KEY",
                message="Immich rejected the configured API key.",
            )
        if auth_response.status_code != 200:
            return ImmichConnectionResult(
                connected=False,
                code="IMMICH_HTTP_ERROR",
                message=(
                    f"Immich returned HTTP {auth_response.status_code} while validating "
                    "the API key."
                ),
            )
        try:
            api_key_payload = auth_response.json()
        except ValueError:
            return ImmichConnectionResult(
                connected=False,
                code="IMMICH_INVALID_RESPONSE",
                message="Immich did not return a valid authenticated API response.",
            )
        permissions = (
            api_key_payload.get("permissions") if isinstance(api_key_payload, dict) else None
        )
        if (
            not isinstance(api_key_payload, dict)
            or not api_key_payload.get("id")
            or not isinstance(permissions, list)
        ):
            return ImmichConnectionResult(
                connected=False,
                code="IMMICH_INVALID_RESPONSE",
                message="Immich did not return an authenticated API response.",
            )

        server_version: str | None = None
        version_response = await active_client.get(
            f"{normalized_url}/api/server/version",
            headers={"Accept": "application/json", "x-api-key": immich_api_key},
        )
        if version_response.status_code == 200:
            try:
                version_payload = version_response.json()
            except ValueError:
                version_payload = None
            if (
                isinstance(version_payload, dict)
                and {"major", "minor", "patch"} <= version_payload.keys()
            ):
                server_version = (
                    f"{version_payload['major']}.{version_payload['minor']}."
                    f"{version_payload['patch']}"
                )
    except (httpx.InvalidURL, httpx.UnsupportedProtocol):
        return ImmichConnectionResult(
            connected=False,
            code="IMMICH_INVALID_URL",
            message="The configured Immich URL could not be used for a request.",
        )
    except httpx.RequestError:
        return ImmichConnectionResult(
            connected=False,
            code="IMMICH_UNREACHABLE",
            message="The Immich server could not be reached at the configured URL.",
        )
    finally:
        if owns_client:
            await active_client.aclose()

    return ImmichConnectionResult(
        connected=True,
        code="IMMICH_CONNECTED",
        message="Connected to Immich successfully.",
        server_version=server_version,
        api_key_permissions=[str(scope) for scope in permissions],
    )


def upload_immich_asset_sync(
    file_path: Path,
    immich_url: str,
    immich_api_key: str,
    *,
    file_created_at: datetime,
    file_modified_at: datetime,
    timeout: httpx.Timeout = IMMICH_UPLOAD_TIMEOUT,
    client: httpx.Client | None = None,
) -> str:
    """Upload a file to Immich, returning its asset id.

    Synchronous by design: this streams a potentially large file's multipart body
    from a plain file handle, which would block the event loop if called directly
    from async code. Callers must run this via the blocking-I/O executor, exactly
    like every other filesystem/CPU-bound operation in the job runner.

    A transport failure here has an indeterminate remote outcome — Immich may have
    already stored the file even though the response never arrived. Retrying the
    same, unchanged file relies on Immich's own checksum-based dedup (a "duplicate"
    upload returns the same asset id) rather than any reconciliation performed here.
    """
    normalized_url = normalize_immich_url(immich_url)
    owns_client = client is None
    active_client = client or httpx.Client(timeout=timeout)
    try:
        with file_path.open("rb") as handle:
            try:
                response = active_client.post(
                    f"{normalized_url}/api/assets",
                    headers={"x-api-key": immich_api_key},
                    data={
                        "fileCreatedAt": file_created_at.astimezone(UTC).isoformat(),
                        "fileModifiedAt": file_modified_at.astimezone(UTC).isoformat(),
                        "filename": file_path.name,
                    },
                    files={"assetData": (file_path.name, handle, "video/mp4")},
                )
            except httpx.RequestError as error:
                raise ImmichUnreachableError(
                    f"The Immich server could not be reached while uploading: {error}"
                ) from error
        if response.status_code in {401, 403}:
            raise ImmichAuthError("Immich rejected the configured API key during upload.")
        if response.status_code not in {200, 201}:
            raise ImmichInvalidResponseError(
                f"Immich returned HTTP {response.status_code} while uploading."
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise ImmichInvalidResponseError(
                "Immich did not return a valid JSON response after upload."
            ) from error
        # Only require a non-empty id — documented response shapes for this
        # endpoint have varied between a `status` enum and a `duplicate` bool
        # across Immich versions, and neither is needed for our own logic.
        asset_id = payload.get("id") if isinstance(payload, dict) else None
        if not asset_id:
            raise ImmichInvalidResponseError("Immich did not return an asset id after upload.")
        return str(asset_id)
    finally:
        if owns_client:
            active_client.close()


async def set_immich_asset_description(
    asset_id: str,
    description: str,
    immich_url: str,
    immich_api_key: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    normalized_url = normalize_immich_url(immich_url)
    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        try:
            response = await active_client.patch(
                f"{normalized_url}/api/assets/{asset_id}",
                headers={"x-api-key": immich_api_key, "Content-Type": "application/json"},
                json={"description": description},
            )
        except httpx.RequestError as error:
            raise ImmichUnreachableError(
                f"The Immich server could not be reached while setting the description: {error}"
            ) from error
        # The deprecated single-asset update endpoint reports a missing/invalid asset
        # id as HTTP 400 in practice, not 404 (confirmed against a live server) — both
        # are treated as "the asset is gone", not a generic invalid-response failure.
        if response.status_code in {400, 404}:
            raise ImmichAssetNotFoundError(f"Immich asset {asset_id} no longer exists.")
        if response.status_code in {401, 403}:
            raise ImmichAuthError(
                "Immich rejected the configured API key while setting the description."
            )
        if response.status_code != 200:
            raise ImmichInvalidResponseError(
                f"Immich returned HTTP {response.status_code} while setting the description."
            )
    finally:
        if owns_client:
            await active_client.aclose()


async def upsert_immich_tags(
    tag_paths: list[str],
    immich_url: str,
    immich_api_key: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, str]:
    """Create or look up one or more `/`-separated hierarchical tag paths.

    Immich creates (or reuses) every ancestor segment of each path automatically —
    this is the "idempotent lookup/create" behavior; nothing extra is needed on our
    side to make repeated calls with the same paths safe. Returns `{path: tag_id}`.
    """
    normalized_url = normalize_immich_url(immich_url)
    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        try:
            response = await active_client.put(
                f"{normalized_url}/api/tags",
                headers={"x-api-key": immich_api_key, "Content-Type": "application/json"},
                json={"tags": tag_paths},
            )
        except httpx.RequestError as error:
            raise ImmichUnreachableError(
                f"The Immich server could not be reached while upserting tags: {error}"
            ) from error
        if response.status_code in {401, 403}:
            raise ImmichAuthError("Immich rejected the configured API key while upserting tags.")
        if response.status_code != 200:
            raise ImmichInvalidResponseError(
                f"Immich returned HTTP {response.status_code} while upserting tags."
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise ImmichInvalidResponseError(
                "Immich did not return a valid JSON response while upserting tags."
            ) from error
        if not isinstance(payload, list):
            raise ImmichInvalidResponseError(
                "Immich did not return a tag list while upserting tags."
            )
        result: dict[str, str] = {}
        for entry in payload:
            if isinstance(entry, dict) and entry.get("id") and entry.get("value"):
                result[str(entry["value"])] = str(entry["id"])
        return result
    finally:
        if owns_client:
            await active_client.aclose()


def _require_bulk_success(
    payload: object, asset_id: str, *, acceptable_errors: set[str], action: str
) -> None:
    """Check one asset's entry in a bulk tag/untag response array.

    HTTP 200 from these endpoints only means the request was well-formed — each
    asset gets its own `{id, success, error?}` result, and a `success: false`
    entry (e.g. `no_permission`, `validation`) must not be treated as done just
    because the surrounding request succeeded.
    """
    if not isinstance(payload, list):
        raise ImmichInvalidResponseError(f"Immich did not return a result list while {action}.")
    entry = next(
        (item for item in payload if isinstance(item, dict) and item.get("id") == asset_id),
        None,
    )
    if entry is None:
        raise ImmichInvalidResponseError(
            f"Immich did not report a result for the asset while {action}."
        )
    if entry.get("success") is True or entry.get("error") in acceptable_errors:
        return
    raise ImmichInvalidResponseError(
        f"Immich reported failure ({entry.get('error') or 'unknown'}) while {action}."
    )


async def tag_immich_assets(
    asset_id: str,
    tag_ids: list[str],
    immich_url: str,
    immich_api_key: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    normalized_url = normalize_immich_url(immich_url)
    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        try:
            response = await active_client.put(
                f"{normalized_url}/api/tags/assets",
                headers={"x-api-key": immich_api_key, "Content-Type": "application/json"},
                json={"assetIds": [asset_id], "tagIds": tag_ids},
            )
        except httpx.RequestError as error:
            raise ImmichUnreachableError(
                f"The Immich server could not be reached while tagging the asset: {error}"
            ) from error
        if response.status_code in {401, 403}:
            raise ImmichAuthError("Immich rejected the configured API key while tagging the asset.")
        if response.status_code != 200:
            raise ImmichInvalidResponseError(
                f"Immich returned HTTP {response.status_code} while tagging the asset."
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise ImmichInvalidResponseError(
                "Immich did not return a valid JSON response while tagging the asset."
            ) from error
        # HTTP 200 only means the request was well-formed — each requested asset
        # gets its own success/error entry, and a false success here would let the
        # runner durably record a tag id as applied when it never actually was.
        _require_bulk_success(
            payload, asset_id, acceptable_errors={"duplicate"}, action="tagging the asset"
        )
    finally:
        if owns_client:
            await active_client.aclose()


async def untag_immich_assets(
    tag_id: str,
    asset_ids: list[str],
    immich_url: str,
    immich_api_key: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Remove one tag from the given assets — used to clean up a tag that a clip
    no longer resolves to (e.g. its library or show name changed since the last
    upload), so re-tagging only ever adds tags without this call would otherwise
    leave stale, superseded tags applied forever."""
    normalized_url = normalize_immich_url(immich_url)
    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        try:
            response = await active_client.request(
                "DELETE",
                f"{normalized_url}/api/tags/{tag_id}/assets",
                headers={"x-api-key": immich_api_key, "Content-Type": "application/json"},
                json={"ids": asset_ids},
            )
        except httpx.RequestError as error:
            raise ImmichUnreachableError(
                f"The Immich server could not be reached while removing a tag: {error}"
            ) from error
        if response.status_code in {401, 403}:
            raise ImmichAuthError("Immich rejected the configured API key while removing a tag.")
        if response.status_code != 200:
            raise ImmichInvalidResponseError(
                f"Immich returned HTTP {response.status_code} while removing a tag."
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise ImmichInvalidResponseError(
                "Immich did not return a valid JSON response while removing a tag."
            ) from error
        for target_asset_id in asset_ids:
            # "not_found" here means the asset already didn't have this tag — the
            # goal (tag not applied) is already achieved, so that's an acceptable
            # outcome, not a failure to report.
            _require_bulk_success(
                payload,
                target_asset_id,
                acceptable_errors={"not_found", "duplicate"},
                action="removing a tag",
            )
    finally:
        if owns_client:
            await active_client.aclose()
