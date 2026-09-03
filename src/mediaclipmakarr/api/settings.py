"""Application settings API routes."""

from fastapi import APIRouter, HTTPException, Request

from mediaclipmakarr.application_settings import (
    ApplicationSettingsResponse,
    ApplicationSettingsUpdate,
    get_effective_application_settings,
    managed_update_fields,
    save_persisted_application_settings,
    serialize_update,
)
from mediaclipmakarr.config import Settings
from mediaclipmakarr.immich import (
    ImmichConnectionRequest,
    ImmichConnectionResult,
    test_immich_connection,
)
from mediaclipmakarr.plex import (
    PlexConnectionRequest,
    PlexConnectionResult,
    test_plex_connection,
)


def build_router(application_settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/api/settings", response_model=ApplicationSettingsResponse)
    async def get_application_settings(request: Request) -> ApplicationSettingsResponse:
        return request.app.state.effective_application_settings.to_response()

    @router.put("/api/settings", response_model=ApplicationSettingsResponse)
    async def update_application_settings(
        update: ApplicationSettingsUpdate, request: Request
    ) -> ApplicationSettingsResponse:
        effective = request.app.state.effective_application_settings
        managed_fields = managed_update_fields(update, effective.environment_managed)
        if managed_fields:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ENVIRONMENT_MANAGED_SETTING",
                    "message": "Environment-managed settings cannot be changed through the API.",
                    "fields": managed_fields,
                },
            )

        changes_plex_url = update.plex_url is not None and update.plex_url != effective.plex_url
        supplies_plex_token = bool(update.plex_token and update.plex_token.strip())
        if (
            changes_plex_url
            and effective.plex_token
            and not supplies_plex_token
            and not update.clear_plex_token
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "PLEX_CREDENTIALS_REQUIRED",
                    "message": "Enter the Plex token again when changing the Plex server URL.",
                },
            )

        values = serialize_update(update)
        if values:
            await save_persisted_application_settings(
                request.app.state.database_engine, values
            )
        updated = await get_effective_application_settings(
            request.app.state.database_engine, application_settings
        )
        request.app.state.effective_application_settings = updated
        return updated.to_response()

    @router.post("/api/settings/plex/test", response_model=PlexConnectionResult)
    async def test_current_plex_connection(
        request: Request, connection: PlexConnectionRequest | None = None
    ) -> PlexConnectionResult:
        effective = request.app.state.effective_application_settings
        if connection is None or connection.plex_url is None or connection.plex_token is None:
            candidate_url = effective.plex_url
            candidate_token = effective.plex_token
        else:
            candidate_url = connection.plex_url
            candidate_token = connection.plex_token.get_secret_value().strip()
        return await test_plex_connection(candidate_url, candidate_token)

    @router.post("/api/settings/immich/test", response_model=ImmichConnectionResult)
    async def test_current_immich_connection(
        request: Request, connection: ImmichConnectionRequest | None = None
    ) -> ImmichConnectionResult:
        effective = request.app.state.effective_application_settings
        if connection is None or connection.immich_url is None or connection.immich_api_key is None:
            candidate_url = effective.immich_url
            candidate_key = effective.immich_api_key
        else:
            candidate_url = connection.immich_url
            candidate_key = connection.immich_api_key.get_secret_value().strip()
        return await test_immich_connection(candidate_url, candidate_key)

    return router
