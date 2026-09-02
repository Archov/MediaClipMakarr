"""Plex session API routes."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from mediaclipmakarr.config import Settings
from mediaclipmakarr.plex import PlexSessionPoller, PlexSessionSnapshot, snapshot_sse_payload
from mediaclipmakarr.session_frames import (
    SessionFrameError,
    cleanup_session_frame_work_dir,
    render_session_frame,
)
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

    @router.get("/api/sessions/{session_identity}/frame")
    async def get_session_frame(
        session_identity: str,
        request: Request,
        media_identity: str = Query(min_length=1),
        position_ms: int = Query(ge=0),
        download: bool = False,
    ) -> FileResponse:
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
        if session.media_identity != media_identity:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "PLEX_MEDIA_CHANGED",
                    "message": (
                        "The selected Plex player changed media before the frame was rendered."
                    ),
                    "retryable": True,
                },
            )
        try:
            async with request.app.state.media_process_gate.slot():
                rendered = await render_session_frame(
                    session,
                    position_ms,
                    "export" if download else "thumbnail",
                    request.app.state.effective_application_settings,
                    application_settings,
                    run_blocking=request.app.state.blocking_io.run,
                )
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
        except SessionFrameError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail={
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                    "context": error.context,
                },
            ) from error
        return FileResponse(
            rendered.path,
            media_type="image/png",
            filename=rendered.filename if download else None,
            content_disposition_type="attachment" if download else "inline",
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
            background=BackgroundTask(cleanup_session_frame_work_dir, rendered.work_dir),
        )

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
