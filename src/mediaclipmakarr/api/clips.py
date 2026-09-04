"""Managed clip API routes."""

from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from mediaclipmakarr.application_settings import normalize_immich_url
from mediaclipmakarr.clip_library import (
    ClipAssetUnavailable,
    ClipDeleteRequest,
    ClipDeleteResult,
    ClipDeleteSafetyError,
    ClipFilterOptions,
    ClipMetadataUpdate,
    ClipPage,
    ClipRecord,
    ClipRevisionConflict,
    build_bulk_immich_upload_plan,
    build_immich_upload_plan,
    build_metadata_edit_plan,
    build_thumbnail_job_plan,
    delete_clip,
    list_clips,
    list_filter_options,
    list_libraries,
    list_unlinked_clip_ids,
    public_clip,
    thumbnail_is_current,
)
from mediaclipmakarr.clips import (
    ClipCreateRequest,
    ClipCreateValidationError,
    get_clip,
    validate_clip_create_request,
)
from mediaclipmakarr.config import Settings
from mediaclipmakarr.jobs import (
    JobSnapshot,
    enqueue_bulk_immich_upload_job,
    enqueue_clip_create_job,
    enqueue_immich_upload_job,
    enqueue_metadata_edit_job,
    enqueue_thumbnail_job,
    get_latest_jobs_for_operations,
)
from mediaclipmakarr.plex import PlexClient, PlexSessionError
from mediaclipmakarr.render_plan import build_clip_render_plan
from mediaclipmakarr.source_media import SourceMediaError, resolve_and_probe_source_media


def build_router(application_settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.post("/api/clips", response_model=JobSnapshot)
    async def create_clip(
        clip_request: ClipCreateRequest, request: Request
    ) -> JobSnapshot:
        try:
            snapshot = request.app.state.plex_session_poller.snapshot
            result = validate_clip_create_request(clip_request, snapshot)
            session = next(
                session
                for session in snapshot.sessions
                if session.session_identity == result.session_identity
            )
            result.source_media = await resolve_and_probe_source_media(
                session,
                request.app.state.effective_application_settings,
                application_settings,
                run_blocking=request.app.state.blocking_io.run,
                requested_audio_stream_index=clip_request.audio_stream_index,
                requested_subtitle_stream_index=clip_request.subtitle_stream_index,
                subtitles_enabled=clip_request.subtitles_enabled,
            )
            render_plan = build_clip_render_plan(
                session=session,
                request=clip_request,
                source_media=result.source_media,
                x264_preset=request.app.state.effective_application_settings.x264_preset,
            )
            job = await enqueue_clip_create_job(
                request.app.state.database_engine,
                render_plan,
            )
            await request.app.state.job_events.publish(job.id, job)
            request.app.state.job_runner.wake()
            return job
        except ClipCreateValidationError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=error.error.model_dump(mode="json"),
            ) from error
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

    @router.get("/api/clips", response_model=ClipPage)
    async def browse_clips(
        request: Request,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=25, ge=1, le=100),
        all_items: bool = Query(default=False, alias="all"),
        search: str | None = Query(default=None, max_length=200),
        library: str | None = Query(default=None, max_length=160),
        media_type: str | None = Query(default=None, pattern="^(movie|episode|video)$"),
        media: Annotated[list[str] | None, Query()] = None,
        episode: Annotated[list[str] | None, Query()] = None,
        sort: str = Query(
            default="newest",
            pattern="^(newest|oldest|title_asc|title_desc|duration_asc|duration_desc)$",
        ),
    ) -> ClipPage:
        clip_page = await list_clips(
            request.app.state.database_engine,
            page=page,
            page_size=None if all_items else page_size,
            search=search,
            library=library,
            media_type=media_type,
            media=media,
            episode=episode,
            sort=sort,
        )
        upload_jobs = await get_latest_jobs_for_operations(
            request.app.state.database_engine,
            "immich_upload",
            [item.id for item in clip_page.items],
        )
        for item in clip_page.items:
            item.immich_upload_job = upload_jobs.get(item.id)
        return clip_page

    @router.get("/api/clips/libraries", response_model=list[str])
    async def clip_libraries(request: Request) -> list[str]:
        return await list_libraries(request.app.state.database_engine)

    @router.get("/api/clips/filter-options", response_model=ClipFilterOptions)
    async def clip_filter_options(request: Request) -> ClipFilterOptions:
        effective = request.app.state.effective_application_settings
        plex_libraries: list[str] = []
        if effective.plex_url and effective.plex_token:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
                    plex_libraries = await PlexClient(
                        effective.plex_url, effective.plex_token, client=client
                    ).fetch_library_names()
            except (PlexSessionError, ValueError):
                # Filtering remains available from durable clip metadata if Plex is offline.
                pass
        return await list_filter_options(
            request.app.state.database_engine, plex_libraries
        )

    @router.get("/api/clips/{clip_id}", response_model=ClipRecord)
    async def clip_detail(clip_id: str, request: Request) -> ClipRecord:
        clip = await get_clip(
            request.app.state.database_engine,
            clip_id,
            application_settings.resolved_clip_dir,
        )
        if clip is None:
            raise HTTPException(status_code=404, detail="Clip not found.")
        upload_jobs = await get_latest_jobs_for_operations(
            request.app.state.database_engine, "immich_upload", [clip_id]
        )
        return public_clip(clip, immich_upload_job=upload_jobs.get(clip_id))

    @router.post("/api/clips/{clip_id}/immich-upload", response_model=JobSnapshot)
    async def upload_clip_to_immich(clip_id: str, request: Request) -> JobSnapshot:
        clip = await get_clip(
            request.app.state.database_engine,
            clip_id,
            application_settings.resolved_clip_dir,
        )
        if clip is None:
            raise HTTPException(status_code=404, detail="Clip not found.")
        effective = request.app.state.effective_application_settings
        if not effective.immich_url or not effective.immich_api_key:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IMMICH_NOT_CONFIGURED",
                    "message": "Configure Immich in Settings before uploading.",
                },
            )
        plan = build_immich_upload_plan(clip)
        job = await enqueue_immich_upload_job(request.app.state.database_engine, plan)
        await request.app.state.job_events.publish(job.id, job)
        request.app.state.job_runner.wake()
        return job

    @router.post("/api/clips/immich-upload/bulk", response_model=JobSnapshot)
    async def bulk_upload_clips_to_immich(request: Request) -> JobSnapshot:
        effective = request.app.state.effective_application_settings
        if not effective.immich_url or not effective.immich_api_key:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IMMICH_NOT_CONFIGURED",
                    "message": "Configure Immich in Settings before uploading.",
                },
            )
        normalized_url = normalize_immich_url(effective.immich_url)
        clip_ids = await list_unlinked_clip_ids(
            request.app.state.database_engine, normalized_url
        )
        plan = build_bulk_immich_upload_plan(clip_ids)
        job = await enqueue_bulk_immich_upload_job(request.app.state.database_engine, plan)
        await request.app.state.job_events.publish(job.id, job)
        request.app.state.job_runner.wake()
        return job

    @router.put("/api/clips/{clip_id}", response_model=JobSnapshot)
    async def edit_clip_metadata(
        clip_id: str, metadata: ClipMetadataUpdate, request: Request
    ) -> JobSnapshot:
        clip = await get_clip(
            request.app.state.database_engine,
            clip_id,
            application_settings.resolved_clip_dir,
        )
        if clip is None:
            raise HTTPException(status_code=404, detail="Clip not found.")
        try:
            plan = await request.app.state.blocking_io.run(
                build_metadata_edit_plan,
                clip,
                metadata,
                application_settings.resolved_clip_dir,
            )
        except ClipRevisionConflict as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": error.job_error_code,
                    "message": str(error),
                    "retryable": False,
                },
            ) from error
        job = await enqueue_metadata_edit_job(request.app.state.database_engine, plan)
        await request.app.state.job_events.publish(job.id, job)
        request.app.state.job_runner.wake()
        return job

    @router.delete("/api/clips/{clip_id}", response_model=ClipDeleteResult)
    async def remove_clip(
        clip_id: str, deletion: ClipDeleteRequest, request: Request
    ) -> ClipDeleteResult:
        try:
            result = await delete_clip(
                request.app.state.database_engine,
                clip_id,
                deletion.expected_revision,
                clip_root=application_settings.resolved_clip_dir,
                thumbnail_root=application_settings.resolved_thumbnail_dir,
                run_blocking=request.app.state.blocking_io.run,
            )
        except ClipRevisionConflict as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": error.job_error_code,
                    "message": str(error),
                    "retryable": False,
                },
            ) from error
        except ClipDeleteSafetyError as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CLIP_DELETE_PATH_REJECTED",
                    "message": str(error),
                    "retryable": False,
                },
            ) from error
        except ClipAssetUnavailable as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": error.job_error_code,
                    "message": str(error),
                    "retryable": True,
                },
            ) from error
        if result is None:
            raise HTTPException(status_code=404, detail="Clip not found.")
        return result

    @router.get("/api/clips/{clip_id}/thumbnail")
    async def clip_thumbnail(clip_id: str, request: Request):
        clip = await get_clip(
            request.app.state.database_engine,
            clip_id,
            application_settings.resolved_clip_dir,
        )
        if clip is None:
            raise HTTPException(status_code=404, detail="Clip not found.")
        try:
            source_stat = await request.app.state.blocking_io.run(
                Path(str(clip["file_path"])).stat
            )
        except OSError as error:
            raise HTTPException(status_code=404, detail="Clip media is unavailable.") from error
        thumbnail_value = clip.get("thumbnail_path")
        thumbnail = await request.app.state.blocking_io.run(
            Path(str(thumbnail_value)).resolve, strict=False
        ) if thumbnail_value else None
        thumbnail_root = application_settings.resolved_thumbnail_dir
        if (
            thumbnail is not None
            and thumbnail.is_relative_to(thumbnail_root)
            and await request.app.state.blocking_io.run(
                thumbnail_is_current, clip, source_stat
            )
        ):
            return FileResponse(thumbnail, media_type="image/jpeg")
        plan = build_thumbnail_job_plan(clip, source_stat)
        job = await enqueue_thumbnail_job(request.app.state.database_engine, plan)
        await request.app.state.job_events.publish(job.id, job)
        request.app.state.job_runner.wake()
        return JSONResponse(
            status_code=202,
            content={"job_id": job.id, "message": "Thumbnail generation is queued."},
            headers={"Retry-After": "2", "Cache-Control": "no-store"},
        )
    @router.get("/api/clips/{clip_id}/media")
    async def play_clip(clip_id: str, request: Request) -> FileResponse:
        clip = await get_clip(
            request.app.state.database_engine,
            clip_id,
            application_settings.resolved_clip_dir,
        )
        if clip is None:
            raise HTTPException(status_code=404, detail="Clip not found.")
        return FileResponse(
            clip["file_path"],
            media_type="video/mp4",
            filename=f"{clip['title']}.mp4",
            content_disposition_type="inline",
        )

    @router.get("/api/clips/{clip_id}/download")
    async def download_clip(clip_id: str, request: Request) -> FileResponse:
        clip = await get_clip(
            request.app.state.database_engine,
            clip_id,
            application_settings.resolved_clip_dir,
        )
        if clip is None:
            raise HTTPException(status_code=404, detail="Clip not found.")
        return FileResponse(
            clip["file_path"],
            media_type="video/mp4",
            filename=f"{clip['title']}.mp4",
            content_disposition_type="attachment",
        )

    return router
