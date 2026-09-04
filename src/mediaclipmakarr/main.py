from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from mediaclipmakarr.api.clips import build_router as build_clips_router
from mediaclipmakarr.api.health import build_router as build_health_router
from mediaclipmakarr.api.jobs import build_router as build_jobs_router
from mediaclipmakarr.api.plex import build_router as build_plex_router
from mediaclipmakarr.api.settings import build_router as build_settings_router
from mediaclipmakarr.application_settings import get_effective_application_settings
from mediaclipmakarr.concurrency import BlockingIOExecutor, MediaProcessGate
from mediaclipmakarr.config import Settings, load_settings, validate_path_layout
from mediaclipmakarr.database import create_database_engine, upgrade_database
from mediaclipmakarr.health import initialize_writable_directories, inspect_media_tools
from mediaclipmakarr.jobs import (
    ImmichJobSettings,
    JobEventBroker,
    JobRunner,
)
from mediaclipmakarr.plex import (
    PlexSessionPoller,
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
    application_settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        executor = BlockingIOExecutor(application_settings.blocking_io_workers)
        app.state.blocking_io = executor
        app.state.media_process_gate = MediaProcessGate()
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
            app.state.effective_application_settings = await get_effective_application_settings(
                database_engine, application_settings
            )

            async def load_cached_application_settings():
                return app.state.effective_application_settings

            async def load_cached_plex_token() -> str | None:
                return app.state.effective_application_settings.plex_token

            async def load_cached_immich_settings() -> ImmichJobSettings:
                settings = app.state.effective_application_settings
                return ImmichJobSettings(
                    url=settings.immich_url,
                    api_key=settings.immich_api_key,
                    default_tag=settings.immich_default_tag,
                    tag_library=settings.immich_tag_library,
                    tag_show=settings.immich_tag_show,
                    tag_episode=settings.immich_tag_episode,
                    auto_upload=settings.immich_auto_upload,
                )

            app.state.plex_session_poller = PlexSessionPoller(
                load_cached_application_settings
            )
            await app.state.plex_session_poller.start()

            app.state.media_tools = await inspect_media_tools(application_settings)
            if app.state.media_tools.status != "ok":
                logger.warning(app.state.media_tools.message)
            for name, error in directory_initialization.items():
                if error and name != "private-data":
                    logger.warning(error)
            app.state.job_events = JobEventBroker()
            app.state.job_runner = JobRunner(
                database_engine,
                application_settings,
                run_blocking=executor.run,
                events=app.state.job_events,
                plex_token_loader=load_cached_plex_token,
                immich_settings_loader=load_cached_immich_settings,
                media_process_gate=app.state.media_process_gate,
            )
            await app.state.job_runner.start()
            yield
        finally:
            if hasattr(app.state, "job_runner"):
                await app.state.job_runner.stop()
            if hasattr(app.state, "plex_session_poller"):
                await app.state.plex_session_poller.stop()
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
    app.include_router(build_health_router(application_settings))
    app.include_router(build_settings_router(application_settings))
    app.include_router(build_plex_router(application_settings))
    app.include_router(build_clips_router(application_settings))
    app.include_router(build_jobs_router())

    @app.exception_handler(RequestValidationError)
    async def redacted_validation_error(
        _request: Request, error: RequestValidationError
    ) -> JSONResponse:
        errors = []
        for validation_error in error.errors():
            sanitized_error = dict(validation_error)
            sanitized_error.pop("input", None)
            errors.append(sanitized_error)
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder({"detail": errors}),
        )

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
