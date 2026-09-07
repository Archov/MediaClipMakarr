"""Plex session API routes."""

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from mediaclipmakarr.aspect_ratio_overrides import (
    AspectRatioIdentity,
    clear_aspect_ratio_override,
    lookup_aspect_ratio_override,
    set_aspect_ratio_override,
)
from mediaclipmakarr.config import Settings
from mediaclipmakarr.crop_detect import parse_aspect_ratio
from mediaclipmakarr.crop_probe import CropDetectionResult, CropProbeError, detect_crop_for_session
from mediaclipmakarr.plex import (
    PlexClient,
    PlexSession,
    PlexSessionError,
    PlexSessionPoller,
    PlexSessionSnapshot,
    snapshot_sse_payload,
)
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
from mediaclipmakarr.source_metadata import derive_aspect_ratio_group_path


class CropDetectRequest(BaseModel):
    start_ms: int
    end_ms: int
    # "auto" (or omitted) runs the cropdetect probe; anything else is parsed
    # as a target ratio ("4:3", "1.85:1", ...) and applied as a fixed,
    # centered crop with no probing.
    aspect_ratio: str | None = None


class CropBox(BaseModel):
    width: int
    height: int
    x: int
    y: int


class CropDetectResponse(BaseModel):
    source_width: int
    source_height: int
    crop: CropBox | None
    aspect_ratio_fraction: str | None
    aspect_ratio_decimal: str | None


class AspectRatioOverrideResponse(BaseModel):
    aspect_ratio: str | None


class AspectRatioOverrideUpdate(BaseModel):
    aspect_ratio: str | None = None


def _find_session(snapshot: PlexSessionSnapshot, session_identity: str) -> PlexSession | None:
    return next(
        (
            candidate
            for candidate in snapshot.sessions
            if candidate.session_identity == session_identity
        ),
        None,
    )


def _session_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": "PLEX_SESSION_NOT_FOUND",
            "message": "The selected Plex session is no longer active.",
            "retryable": True,
        },
    )


async def _resolve_aspect_ratio_identity(
    session: PlexSession, effective_settings
) -> AspectRatioIdentity:
    """Best-effort identity for the persisted-override lookup — external ids
    and the show/movie-level rating key both require an extra Plex API call
    that's skipped if Plex isn't reachable or the item has no rating key; the
    path key alone (or nothing at all) is still a usable, if weaker, identity."""
    path_key = (
        derive_aspect_ratio_group_path(session.plex_part_file, session.media_type)
        if session.plex_part_file
        else None
    )
    external_ids: list[str] = []
    group_rating_key: str | None = None
    if effective_settings.plex_url and effective_settings.plex_token and session.plex_rating_key:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                plex_client = PlexClient(
                    effective_settings.plex_url, effective_settings.plex_token, client=client
                )
                part_metadata = await plex_client.fetch_media_part_metadata(
                    session.plex_rating_key, part_id=session.plex_part_id
                )
        except PlexSessionError:
            part_metadata = None
        if part_metadata is not None:
            external_ids = part_metadata.external_ids
            group_rating_key = part_metadata.group_rating_key
    if group_rating_key is None and session.media_type == "movie":
        # A movie has no grandparent — its own rating key already *is* the
        # group-level key library metadata would otherwise have supplied.
        group_rating_key = session.plex_rating_key
    return AspectRatioIdentity(
        external_ids=external_ids, path_key=path_key, plex_rating_key=group_rating_key
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
        crop_width: int | None = Query(default=None, gt=0),
        crop_height: int | None = Query(default=None, gt=0),
        crop_x: int | None = Query(default=None, ge=0),
        crop_y: int | None = Query(default=None, ge=0),
    ) -> FileResponse:
        snapshot = request.app.state.plex_session_poller.snapshot
        session = _find_session(snapshot, session_identity)
        if session is None:
            raise _session_not_found()
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
        crop_values = (crop_width, crop_height, crop_x, crop_y)
        if any(value is not None for value in crop_values) and any(
            value is None for value in crop_values
        ):
            raise HTTPException(
                status_code=422,
                detail="crop_width, crop_height, crop_x, and crop_y must be given together.",
            )
        crop = crop_values if crop_width is not None else None
        try:
            async with request.app.state.media_process_gate.slot():
                rendered = await render_session_frame(
                    session,
                    position_ms,
                    "export" if download else "thumbnail",
                    request.app.state.effective_application_settings,
                    application_settings,
                    run_blocking=request.app.state.blocking_io.run,
                    crop=crop,
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

    @router.post(
        "/api/sessions/{session_identity}/crop-detect", response_model=CropDetectResponse
    )
    async def crop_detect(
        session_identity: str, body: CropDetectRequest, request: Request
    ) -> CropDetectResponse:
        snapshot = request.app.state.plex_session_poller.snapshot
        session = _find_session(snapshot, session_identity)
        if session is None:
            raise _session_not_found()
        try:
            async with request.app.state.media_process_gate.slot():
                result: CropDetectionResult = await detect_crop_for_session(
                    session,
                    body.start_ms,
                    body.end_ms,
                    body.aspect_ratio,
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
        except CropProbeError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail={
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                },
            ) from error
        crop = (
            CropBox(
                width=result.crop_width,
                height=result.crop_height,
                x=result.crop_x,
                y=result.crop_y,
            )
            if result.crop_width is not None
            else None
        )
        return CropDetectResponse(
            source_width=result.source_width,
            source_height=result.source_height,
            crop=crop,
            aspect_ratio_fraction=result.aspect_ratio_fraction,
            aspect_ratio_decimal=result.aspect_ratio_decimal,
        )

    @router.get(
        "/api/sessions/{session_identity}/aspect-ratio-override",
        response_model=AspectRatioOverrideResponse,
    )
    async def get_aspect_ratio_override(
        session_identity: str, request: Request
    ) -> AspectRatioOverrideResponse:
        snapshot = request.app.state.plex_session_poller.snapshot
        session = _find_session(snapshot, session_identity)
        if session is None:
            raise _session_not_found()
        identity = await _resolve_aspect_ratio_identity(
            session, request.app.state.effective_application_settings
        )
        aspect_ratio = await lookup_aspect_ratio_override(
            request.app.state.database_engine, identity
        )
        return AspectRatioOverrideResponse(aspect_ratio=aspect_ratio)

    @router.put(
        "/api/sessions/{session_identity}/aspect-ratio-override",
        response_model=AspectRatioOverrideResponse,
    )
    async def put_aspect_ratio_override(
        session_identity: str, body: AspectRatioOverrideUpdate, request: Request
    ) -> AspectRatioOverrideResponse:
        snapshot = request.app.state.plex_session_poller.snapshot
        session = _find_session(snapshot, session_identity)
        if session is None:
            raise _session_not_found()
        identity = await _resolve_aspect_ratio_identity(
            session, request.app.state.effective_application_settings
        )
        if body.aspect_ratio is None:
            await clear_aspect_ratio_override(request.app.state.database_engine, identity)
            return AspectRatioOverrideResponse(aspect_ratio=None)
        try:
            parse_aspect_ratio(body.aspect_ratio)
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "CROP_ASPECT_RATIO_INVALID", "message": str(error)},
            ) from error
        await set_aspect_ratio_override(
            request.app.state.database_engine, identity, body.aspect_ratio
        )
        return AspectRatioOverrideResponse(aspect_ratio=body.aspect_ratio)

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
