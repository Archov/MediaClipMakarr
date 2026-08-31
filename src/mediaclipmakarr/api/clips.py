"""Managed clip API routes."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from mediaclipmakarr.clips import (
    ClipCreateRequest,
    ClipCreateValidationError,
    get_clip,
    validate_clip_create_request,
)
from mediaclipmakarr.config import Settings
from mediaclipmakarr.jobs import JobSnapshot, enqueue_clip_create_job
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
