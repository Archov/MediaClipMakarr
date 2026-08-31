"""Plex session API routes."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from mediaclipmakarr.config import Settings
from mediaclipmakarr.plex import PlexSessionPoller, PlexSessionSnapshot, snapshot_sse_payload
from mediaclipmakarr.source_media import (
    MediaCapabilities,
    SourceMediaError,
    resolve_media_capabilities,
)


def build_router(application_settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/api/sessions", response_model=PlexSessionSnapshot)
    async def get_current_plex_sessions(request: Request) -> PlexSessionSnapshot:
        return request.app.state.plex_session_poller.snapshot

    @router.get(
        "/api/sessions/{session_identity}/media-capabilities",
        response_model=MediaCapabilities,
    )
    async def get_media_capabilities(
        session_identity: str, request: Request
    ) -> MediaCapabilities:
        snapshot = request.app.state.plex_session_poller.snapshot
        session = next(
            (
                candidate
                for candidate in snapshot.sessions
                if candidate.session_identity == session_identity
            ),
            None,
        )
        if session is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "PLEX_SESSION_NOT_FOUND",
                    "message": "The selected Plex session is no longer active.",
                    "retryable": True,
                },
            )
        try:
            source_media = await resolve_media_capabilities(
                session,
                request.app.state.effective_application_settings,
                application_settings,
                run_blocking=request.app.state.blocking_io.run,
            )
            if source_media.capabilities is None:
                raise RuntimeError("Source media capabilities were not produced.")
            return source_media.capabilities
        except SourceMediaError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail={
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                    "alternatives": error.alternatives,
                },
            ) from error

    @router.get("/api/sessions/events")
    async def stream_plex_sessions(request: Request) -> StreamingResponse:
        async def events():
            poller: PlexSessionPoller = request.app.state.plex_session_poller
            version = poller.version
            yield snapshot_sse_payload(poller.snapshot)
            while not await request.is_disconnected():
                snapshot, version, changed = await poller.wait_for_change(
                    version, timeout_seconds=15.0
                )
                if changed:
                    yield snapshot_sse_payload(snapshot)
                else:
                    yield ": keep-alive\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
