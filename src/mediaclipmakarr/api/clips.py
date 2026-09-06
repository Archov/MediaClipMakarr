"""Managed clip API routes."""

import secrets
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

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
    ImmichAssetCheckResult,
    ImmichAssetDeleteResult,
    ImmichDeleteMissingPermission,
    build_bulk_immich_upload_plan,
    build_gif_job_plan,
    build_immich_upload_plan,
    build_metadata_edit_plan,
    build_thumbnail_job_plan,
    delete_clip,
    gif_path,
    gif_url,
    list_all_clip_ids,
    list_clips,
    list_filter_options,
    list_libraries,
    public_clip,
    thumbnail_is_current,
)
from mediaclipmakarr.clips import (
    ClipCreateRequest,
    ClipCreateValidationError,
    clear_clip_immich_asset_id,
    get_clip,
    validate_clip_create_request,
)
from mediaclipmakarr.config import Settings
from mediaclipmakarr.immich import (
    ImmichApiError,
    ImmichAssetNotFoundError,
    ImmichAuthError,
    build_immich_api_key_settings_url,
    build_immich_asset_url,
    delete_immich_asset,
    fetch_immich_api_key_permissions,
    read_immich_asset,
)
from mediaclipmakarr.jobs import (
    JobSnapshot,
    enqueue_bulk_immich_upload_job,
    enqueue_clip_create_job,
    enqueue_gif_job,
    enqueue_immich_upload_job,
    enqueue_metadata_edit_job,
    enqueue_thumbnail_job,
    get_latest_jobs_for_operations,
)
from mediaclipmakarr.plex import PlexClient, PlexSessionError
from mediaclipmakarr.render_plan import build_clip_render_plan
from mediaclipmakarr.source_media import SourceMediaError, resolve_and_probe_source_media


class GifExportResponse(BaseModel):
    """A cache hit resolves synchronously (`status="cached"`); a miss enqueues a
    `gif_export` job for the caller to poll like any other job, whose own result
    payload carries the same `gif_url`/`size_bytes` shape once it succeeds."""

    status: Literal["cached", "queued"]
    gif_url: str | None = None
    size_bytes: int | None = None
    job: JobSnapshot | None = None


def _validated_gif_range(
    start_ms: int | None, end_ms: int | None, duration_ms: int
) -> tuple[int, int] | None:
    """Both-or-neither: `None` means "export the whole clip". Given both, they
    must describe a valid, in-bounds sub-range (a trim-editor export of the
    current selection, never a new clip file)."""
    if start_ms is None and end_ms is None:
        return None
    if start_ms is None or end_ms is None:
        raise HTTPException(
            status_code=400, detail="start_ms and end_ms must be provided together."
        )
    if start_ms < 0 or end_ms <= start_ms or end_ms > duration_ms:
        raise HTTPException(status_code=400, detail="The requested GIF range is invalid.")
    return start_ms, end_ms


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

    @router.post("/api/clips/{clip_id}/immich-check", response_model=ImmichAssetCheckResult)
    async def check_immich_asset(clip_id: str, request: Request) -> ImmichAssetCheckResult:
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
                    "message": "Configure Immich in Settings before opening this clip in Immich.",
                },
            )
        normalized_url = normalize_immich_url(effective.immich_url)
        asset_id = clip.get("immich_asset_id")
        if not asset_id or clip.get("immich_server_url") != normalized_url:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IMMICH_NOT_LINKED",
                    "message": "This clip is not linked to the currently configured Immich server.",
                },
            )
        try:
            await read_immich_asset(str(asset_id), normalized_url, effective.immich_api_key)
        except (ImmichAssetNotFoundError, ImmichAuthError):
            # A missing `asset.read` scope surfaces from this endpoint as either
            # shape depending on server version — a not-found-shaped 400/404, or
            # (confirmed against a live server) a 401/403 auth rejection. Either
            # way, the actual cause is ambiguous until checked against the key's
            # own granted permissions below.
            try:
                permissions = set(
                    await fetch_immich_api_key_permissions(
                        normalized_url, effective.immich_api_key
                    )
                )
            except ImmichApiError as error:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": error.job_error_code,
                        "message": str(error),
                        "retryable": error.job_retryable,
                    },
                ) from error
            if "all" in permissions or "asset.read" in permissions:
                # Confirmed gone (not just an unreadable/permission-denied
                # asset) — clear the stale association immediately rather
                # than waiting on the user to dismiss anything. Only if it's
                # still the same association just checked — a concurrent run
                # may have already replaced it with a newer, valid one.
                cleared = await clear_clip_immich_asset_id(
                    request.app.state.database_engine, clip_id, expected_asset_id=str(asset_id)
                )
                if not cleared:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "IMMICH_ASSOCIATION_CHANGED",
                            "message": (
                                "This clip's Immich association changed during the check "
                                "— try again."
                            ),
                            "retryable": True,
                        },
                    ) from None
                return ImmichAssetCheckResult(status="asset_missing")
            return ImmichAssetCheckResult(
                status="missing_permission",
                settings_url=build_immich_api_key_settings_url(normalized_url),
            )
        except ImmichApiError as error:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": error.job_error_code,
                    "message": str(error),
                    "retryable": error.job_retryable,
                },
            ) from error
        return ImmichAssetCheckResult(
            status="ok", open_url=build_immich_asset_url(normalized_url, str(asset_id))
        )

    @router.post("/api/clips/{clip_id}/immich-reupload", response_model=JobSnapshot)
    async def reupload_clip_to_immich(clip_id: str, request: Request) -> JobSnapshot:
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
        # The plain upload route's "reusing" guard refuses to overwrite an
        # association still pointing at the same server — this route exists
        # specifically for a confirmed-dead asset, so clear it first. Only if
        # it's still the same association just seen — a concurrent run may
        # have already replaced it with a newer, valid one.
        existing_asset_id = clip.get("immich_asset_id")
        if existing_asset_id is not None:
            cleared = await clear_clip_immich_asset_id(
                request.app.state.database_engine,
                clip_id,
                expected_asset_id=str(existing_asset_id),
            )
            if not cleared:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "IMMICH_ASSOCIATION_CHANGED",
                        "message": "This clip's Immich association changed — try again.",
                        "retryable": True,
                    },
                )
        plan = build_immich_upload_plan(clip)
        job = await enqueue_immich_upload_job(request.app.state.database_engine, plan)
        await request.app.state.job_events.publish(job.id, job)
        request.app.state.job_runner.wake()
        return job

    @router.post("/api/immich/delete-retries/{token}", response_model=ImmichAssetDeleteResult)
    async def retry_immich_asset_delete(token: str, request: Request) -> ImmichAssetDeleteResult:
        # Not scoped under /api/clips/{clip_id} — by the time a retry is
        # offered, the local clip is already deleted (see remove_clip). Keyed
        # on an opaque, server-issued token rather than a caller-supplied
        # Immich asset id: accepting a bare asset id here would let anyone
        # reaching this API direct a delete at an arbitrary Immich asset, not
        # just one this app's own delete flow actually orphaned.
        pending = request.app.state.immich_pending_asset_deletes
        asset_id = pending.get(token)
        if asset_id is None:
            raise HTTPException(
                status_code=404,
                detail="This delete retry is unknown or has already been resolved.",
            )
        effective = request.app.state.effective_application_settings
        if not effective.immich_url or not effective.immich_api_key:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IMMICH_NOT_CONFIGURED",
                    "message": "Configure Immich in Settings before retrying this delete.",
                },
            )
        normalized_url = normalize_immich_url(effective.immich_url)
        try:
            await delete_immich_asset(asset_id, effective.immich_url, effective.immich_api_key)
        except ImmichAssetNotFoundError:
            pass
        except ImmichAuthError as auth_error:
            try:
                permissions = set(
                    await fetch_immich_api_key_permissions(
                        normalized_url, effective.immich_api_key
                    )
                )
            except ImmichApiError as error:
                # Leave the token valid — the underlying condition is still
                # unresolved, so a future retry should get another attempt.
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": error.job_error_code,
                        "message": str(error),
                        "retryable": error.job_retryable,
                    },
                ) from error
            if "all" in permissions or "asset.delete" in permissions:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "IMMICH_AUTH_FAILED",
                        "message": str(auth_error),
                        "retryable": True,
                    },
                ) from auth_error
            # Still missing `asset.delete` — keep the token valid for another
            # attempt once the key is actually fixed.
            return ImmichAssetDeleteResult(
                status="missing_permission",
                settings_url=build_immich_api_key_settings_url(normalized_url),
            )
        except ImmichApiError as error:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": error.job_error_code,
                    "message": str(error),
                    "retryable": error.job_retryable,
                },
            ) from error
        # Resolved (deleted or already gone) — the token is single-use.
        pending.pop(token, None)
        return ImmichAssetDeleteResult(status="ok")

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
        clip_ids = await list_all_clip_ids(request.app.state.database_engine)
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
        engine = request.app.state.database_engine
        effective = request.app.state.effective_application_settings
        # Captured before local deletion — there's nothing left to read from
        # once the clip row is gone. Only populated when remote deletion was
        # actually requested and the clip is linked to the *current* server,
        # so a stale/foreign association is never touched.
        immich_asset_id: str | None = None
        if deletion.delete_from_immich and effective.immich_url and effective.immich_api_key:
            existing = await get_clip(engine, clip_id, application_settings.resolved_clip_dir)
            if existing is not None:
                normalized_url = normalize_immich_url(effective.immich_url)
                if (
                    existing.get("immich_asset_id")
                    and existing.get("immich_server_url") == normalized_url
                ):
                    immich_asset_id = str(existing["immich_asset_id"])
        try:
            result = await delete_clip(
                engine,
                clip_id,
                deletion.expected_revision,
                clip_root=application_settings.resolved_clip_dir,
                thumbnail_root=application_settings.resolved_thumbnail_dir,
                gif_root=application_settings.resolved_gif_dir,
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
        # Local delete is authoritative and already committed above — a remote
        # failure here is reported, never rolled back into it. A 404 (already
        # gone from Immich) is the goal already achieved, not a failure.
        if immich_asset_id is not None:
            try:
                await delete_immich_asset(
                    immich_asset_id, effective.immich_url, effective.immich_api_key
                )
            except ImmichAssetNotFoundError:
                pass
            except ImmichAuthError as error:
                # A missing `asset.delete` scope specifically gets a targeted
                # fix-and-retry dialog instead of a plain warning — the same
                # disambiguation `check_immich_asset` runs for `asset.read`.
                missing_permission = False
                try:
                    permissions = set(
                        await fetch_immich_api_key_permissions(
                            normalize_immich_url(effective.immich_url), effective.immich_api_key
                        )
                    )
                    missing_permission = not ({"all", "asset.delete"} & permissions)
                except ImmichApiError:
                    pass
                if missing_permission:
                    # Issue an opaque token rather than handing the caller the
                    # raw asset id — that id is never accepted back from the
                    # client for the retry (see retry_immich_asset_delete).
                    retry_token = secrets.token_urlsafe(32)
                    request.app.state.immich_pending_asset_deletes[retry_token] = immich_asset_id
                    result.immich_delete_missing_permission = ImmichDeleteMissingPermission(
                        retry_token=retry_token,
                        settings_url=build_immich_api_key_settings_url(
                            normalize_immich_url(effective.immich_url)
                        ),
                    )
                else:
                    result.cleanup_warnings.append(
                        f"The clip was deleted, but the Immich asset could not be removed: {error}"
                    )
            except ImmichApiError as error:
                result.cleanup_warnings.append(
                    f"The clip was deleted, but the Immich asset could not be removed: {error}"
                )
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

    @router.post("/api/clips/{clip_id}/gif", response_model=GifExportResponse)
    async def export_clip_gif(
        clip_id: str,
        request: Request,
        size_limit_bytes: Annotated[int | None, Query(gt=0)] = None,
        start_ms: Annotated[int | None, Query(ge=0)] = None,
        end_ms: Annotated[int | None, Query(gt=0)] = None,
    ) -> GifExportResponse:
        clip = await get_clip(
            request.app.state.database_engine,
            clip_id,
            application_settings.resolved_clip_dir,
        )
        if clip is None:
            raise HTTPException(status_code=404, detail="Clip not found.")
        gif_range = _validated_gif_range(start_ms, end_ms, int(clip["duration_ms"]))
        try:
            source_stat = await request.app.state.blocking_io.run(
                Path(str(clip["file_path"])).stat
            )
        except OSError as error:
            raise HTTPException(status_code=404, detail="Clip media is unavailable.") from error
        effective_limit = size_limit_bytes or application_settings.gif_size_limit_bytes
        range_start, range_end = gif_range if gif_range else (None, None)
        destination = gif_path(
            application_settings.resolved_gif_dir,
            clip_id,
            int(clip["revision"]),
            source_stat.st_size,
            source_stat.st_mtime_ns,
            effective_limit,
            range_start,
            range_end,
        )
        if await request.app.state.blocking_io.run(destination.is_file):
            gif_stat = await request.app.state.blocking_io.run(destination.stat)
            return GifExportResponse(
                status="cached",
                gif_url=gif_url(clip_id, effective_limit, range_start, range_end),
                size_bytes=gif_stat.st_size,
            )
        plan = build_gif_job_plan(clip, source_stat, effective_limit, range_start, range_end)
        job = await enqueue_gif_job(request.app.state.database_engine, plan)
        await request.app.state.job_events.publish(job.id, job)
        request.app.state.job_runner.wake()
        return GifExportResponse(status="queued", job=job)

    @router.get("/api/clips/{clip_id}/gif")
    async def clip_gif(
        clip_id: str,
        request: Request,
        size_limit_bytes: Annotated[int | None, Query(gt=0)] = None,
        start_ms: Annotated[int | None, Query(ge=0)] = None,
        end_ms: Annotated[int | None, Query(gt=0)] = None,
    ) -> FileResponse:
        clip = await get_clip(
            request.app.state.database_engine,
            clip_id,
            application_settings.resolved_clip_dir,
        )
        if clip is None:
            raise HTTPException(status_code=404, detail="Clip not found.")
        gif_range = _validated_gif_range(start_ms, end_ms, int(clip["duration_ms"]))
        try:
            source_stat = await request.app.state.blocking_io.run(
                Path(str(clip["file_path"])).stat
            )
        except OSError as error:
            raise HTTPException(status_code=404, detail="Clip media is unavailable.") from error
        effective_limit = size_limit_bytes or application_settings.gif_size_limit_bytes
        range_start, range_end = gif_range if gif_range else (None, None)
        destination = gif_path(
            application_settings.resolved_gif_dir,
            clip_id,
            int(clip["revision"]),
            source_stat.st_size,
            source_stat.st_mtime_ns,
            effective_limit,
            range_start,
            range_end,
        )
        if not await request.app.state.blocking_io.run(destination.is_file):
            raise HTTPException(status_code=404, detail="A GIF has not been generated yet.")
        return FileResponse(
            destination,
            media_type="image/gif",
            filename=f"{clip['title']}.gif",
            content_disposition_type="attachment",
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
