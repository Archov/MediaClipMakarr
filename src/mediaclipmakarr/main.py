from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from mediaclipmakarr.application_settings import (
    ApplicationSettingsResponse,
    ApplicationSettingsUpdate,
    get_effective_application_settings,
    managed_update_fields,
    save_persisted_application_settings,
    serialize_update,
)
from mediaclipmakarr.concurrency import BlockingIOExecutor
from mediaclipmakarr.config import Settings, validate_path_layout
from mediaclipmakarr.database import check_database, create_database_engine, upgrade_database
from mediaclipmakarr.health import (
    ComponentHealth,
    HealthResponse,
    initialize_writable_directories,
    inspect_directories,
    inspect_media_tools,
)
from mediaclipmakarr.plex import (
    PlexConnectionRequest,
    PlexConnectionResult,
    test_plex_connection,
)
from mediaclipmakarr.process_lock import ProcessLock

logger = logging.getLogger(__name__)


class SPAStaticFiles(StaticFiles):
    """Serve Vite assets normally and fall back to index.html for client routes."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            raw_path = scope.get("raw_path", b"")
            request_path = (
                raw_path.decode("utf-8", errors="ignore")
                if isinstance(raw_path, bytes)
                else scope.get("path", path)
            )
            normalized_path = request_path.lstrip("/")
            is_api_path = normalized_path == "api" or normalized_path.startswith("api/")
            is_spa_route = error.status_code == 404 and not is_api_path
            if not is_spa_route:
                raise
            return await super().get_response("index.html", scope)


def create_app(settings: Settings | None = None) -> FastAPI:
    application_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        executor = BlockingIOExecutor(application_settings.blocking_io_workers)
        app.state.blocking_io = executor
        process_lock: ProcessLock | None = None
        database_engine = None
        try:
            validate_path_layout(application_settings)
            directory_initialization = await executor.run(
                initialize_writable_directories, application_settings
            )
            if private_error := directory_initialization["private-data"]:
                raise RuntimeError(private_error)

            process_lock = ProcessLock(application_settings.process_lock_path)
            await executor.run(process_lock.acquire)
            app.state.process_lock = process_lock

            await executor.run(
                upgrade_database,
                application_settings.database_path,
                application_settings.resolved_alembic_ini_path,
                application_settings.resolved_alembic_script_dir,
            )
            database_engine = create_database_engine(application_settings.database_path)
            app.state.database_engine = database_engine

            app.state.media_tools = await inspect_media_tools(application_settings)
            if app.state.media_tools.status != "ok":
                logger.warning(app.state.media_tools.message)
            for name, error in directory_initialization.items():
                if error and name != "private-data":
                    logger.warning(error)
            yield
        finally:
            if database_engine is not None:
                await database_engine.dispose()
            if process_lock is not None:
                await executor.run(process_lock.release)
            await executor.shutdown()

    app = FastAPI(
        title=application_settings.app_name,
        version=application_settings.app_version,
        lifespan=lifespan,
    )
    app.state.settings = application_settings

    @app.get("/api/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        database_ok, revision = await check_database(request.app.state.database_engine)
        database = ComponentHealth(
            status="ok" if database_ok else "error",
            message=(
                "SQLite is reachable and migrations are current."
                if database_ok
                else "SQLite is unavailable. Check the private-data mount and application logs."
            ),
            details={"schema_revision": revision or "unknown"},
        )
        directories = await request.app.state.blocking_io.run(
            inspect_directories, application_settings
        )
        media_tools = request.app.state.media_tools.as_component()
        is_ok = database_ok and media_tools.status == "ok" and all(
            directory.status == "ok" for directory in directories
        )
        return HealthResponse(
            status="ok" if is_ok else "degraded",
            application=ComponentHealth(
                status="ok",
                message="The application process is running with the exclusive data lock.",
                details={
                    "name": application_settings.app_name,
                    "version": application_settings.app_version,
                    "exclusive_lock": request.app.state.process_lock.acquired,
                    "blocking_io_workers": application_settings.blocking_io_workers,
                },
            ),
            database=database,
            media_tools=media_tools,
            directories=directories,
        )

    @app.get("/api/settings", response_model=ApplicationSettingsResponse)
    async def get_application_settings(request: Request) -> ApplicationSettingsResponse:
        effective = await get_effective_application_settings(
            request.app.state.database_engine, application_settings
        )
        return effective.to_response()

    @app.put("/api/settings", response_model=ApplicationSettingsResponse)
    async def update_application_settings(
        update: ApplicationSettingsUpdate, request: Request
    ) -> ApplicationSettingsResponse:
        effective = await get_effective_application_settings(
            request.app.state.database_engine, application_settings
        )
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

        values = serialize_update(update)
        if values:
            await save_persisted_application_settings(
                request.app.state.database_engine, values
            )
        updated = await get_effective_application_settings(
            request.app.state.database_engine, application_settings
        )
        return updated.to_response()

    @app.post("/api/settings/plex/test", response_model=PlexConnectionResult)
    async def test_current_plex_connection(
        request: Request, connection: PlexConnectionRequest | None = None
    ) -> PlexConnectionResult:
        effective = await get_effective_application_settings(
            request.app.state.database_engine, application_settings
        )
        candidate_url = (
            connection.plex_url
            if connection is not None and connection.plex_url is not None
            else effective.plex_url
        )
        candidate_token = (
            connection.plex_token.get_secret_value().strip()
            if connection is not None and connection.plex_token is not None
            else effective.plex_token
        )
        return await test_plex_connection(candidate_url, candidate_token)

    frontend_dist = application_settings.resolved_frontend_dist_dir
    if frontend_dist.is_dir():
        app.mount("/", SPAStaticFiles(directory=frontend_dist, html=True), name="frontend")
    else:

        @app.get("/", include_in_schema=False)
        async def frontend_not_built() -> JSONResponse:
            return JSONResponse(
                {
                    "message": (
                        "MediaClipMakarr API is running. Start the Vite development server or "
                        "build frontend/dist for production SPA serving."
                    )
                }
            )

    return app


app = create_app()
