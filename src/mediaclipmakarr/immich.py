from __future__ import annotations

import logging
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


class ImmichConnectionResult(BaseModel):
    connected: bool
    code: str
    message: str
    server_version: str | None = None


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

        auth_response = await active_client.get(
            f"{normalized_url}/api/users/me",
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
            user_payload = auth_response.json()
        except ValueError:
            return ImmichConnectionResult(
                connected=False,
                code="IMMICH_INVALID_RESPONSE",
                message="Immich did not return a valid authenticated API response.",
            )
        if not isinstance(user_payload, dict) or not user_payload.get("id"):
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
    )
