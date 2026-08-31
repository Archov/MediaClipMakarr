"""Health API routes."""

from fastapi import APIRouter, Request

from mediaclipmakarr.config import Settings
from mediaclipmakarr.database import check_database
from mediaclipmakarr.health import ComponentHealth, HealthResponse, inspect_directories


def build_router(application_settings: Settings) -> APIRouter:
    router = APIRouter()
    @router.get("/api/health", response_model=HealthResponse)
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


    return router

