from __future__ import annotations

from xml.etree import ElementTree

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr

from mediaclipmakarr.application_settings import normalize_plex_url


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
