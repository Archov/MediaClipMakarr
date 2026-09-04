"""Sequential background-job execution orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mediaclipmakarr.application_settings import normalize_immich_url
from mediaclipmakarr.clip_library import (
    BulkImmichUploadJobPlan,
    ClipRevisionConflict,
    ImmichUploadJobPlan,
    MetadataEditJobPlan,
    ThumbnailJobPlan,
    build_immich_tag_paths,
    build_immich_upload_plan,
    build_thumbnail_job_plan,
    generate_thumbnail,
    rewrite_clip_metadata,
    thumbnail_path,
)
from mediaclipmakarr.clips import (
    get_clip,
    insert_clip,
    parse_stored_immich_tag_ids,
    set_clip_immich_asset_id,
    set_clip_immich_tag_ids,
)
from mediaclipmakarr.concurrency import MediaProcessGate
from mediaclipmakarr.config import Settings
from mediaclipmakarr.immich import (
    ImmichApiError,
    ImmichAssetNotFoundError,
    set_immich_asset_description,
    tag_immich_assets,
    untag_immich_assets,
    upload_immich_asset_sync,
    upsert_immich_tags,
)
from mediaclipmakarr.media_renderer import RenderedClipFile, render_clip_file
from mediaclipmakarr.render_plan import ClipRenderPlan, resolve_unique_clip_path

from .events import JobEventBroker
from .finalization import install_metadata_revision, install_rendered_clip, remove_superseded_clip
from .models import BlockingRunner, ClaimedJob, JobError, JobSnapshot, JobStage
from .recovery import fail_abandoned_jobs, recover_finalizing_jobs
from .repository import (
    _live_progress_snapshot,
    claim_next_job,
    commit_metadata_edit,
    create_pending_metadata_operation,
    create_pending_operation,
    enqueue_immich_upload_job,
    enqueue_thumbnail_job,
    fail_job,
    finish_job_success,
    finish_running_job_partial,
    finish_running_job_success,
    get_job_snapshot,
    transition_to_finalizing,
    update_running_job,
    utc_now,
)

logger = logging.getLogger(__name__)
STALE_WORKDIR_REAP_INTERVAL_SECONDS = 3_600
STALE_WORKDIR_AGE_SECONDS = 24 * 3_600
ClipRenderer = Callable[..., Awaitable[RenderedClipFile]]
PlexTokenLoader = Callable[[], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class ImmichJobSettings:
    url: str | None
    api_key: str | None
    default_tag: str
    tag_library: bool
    tag_show: bool
    tag_episode: bool
    auto_upload: bool


ImmichSettingsLoader = Callable[[], Awaitable[ImmichJobSettings]]


@dataclass(frozen=True, slots=True)
class ImmichOrganizeResult:
    """The outcome of uploading+organizing one clip in Immich, independent of
    which job (the single-clip job or the bulk job) is reporting it."""

    asset_id: str | None
    state: Literal["SUCCEEDED", "PARTIAL", "FAILED"]
    error: JobError | None
    message: str
    result_payload: dict[str, Any]


def _as_utc_datetime(value: Any) -> datetime:
    """Clip rows come back from raw SQL as either a `datetime` or an ISO string,
    always naive-but-UTC per this codebase's `utc_now()` convention — normalize
    either shape into a timezone-aware UTC datetime."""
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class _ImmichNotConfiguredError(RuntimeError):
    """Immich URL/API key were missing when the upload job actually ran.

    The API route already checks this before enqueueing; this only covers a
    settings change landing between enqueue and execution.
    """

    job_error_code = "IMMICH_NOT_CONFIGURED"
    job_retryable = True


def _remove_job_workdir(workdir: Path) -> None:
    shutil.rmtree(workdir, ignore_errors=True)


def _remove_stale_job_workdirs(
    jobs_dir: Path, active_job_ids: set[str], stale_before: float
) -> list[Path]:
    if not jobs_dir.is_dir():
        return []
    removed: list[Path] = []
    for candidate in jobs_dir.iterdir():
        if (
            not candidate.name.startswith("job-")
            or candidate.name in active_job_ids
            or candidate.is_symlink()
        ):
            continue
        try:
            if not candidate.is_dir() or candidate.stat().st_mtime > stale_before:
                continue
        except OSError:
            continue
        shutil.rmtree(candidate, ignore_errors=True)
        removed.append(candidate)
    return removed


class JobRunner:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: Settings,
        *,
        run_blocking: BlockingRunner,
        events: JobEventBroker,
        renderer: ClipRenderer = render_clip_file,
        plex_token_loader: PlexTokenLoader | None = None,
        immich_settings_loader: ImmichSettingsLoader | None = None,
        progress_persist_interval_seconds: float = 1.0,
        media_process_gate: MediaProcessGate | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.run_blocking = run_blocking
        self.events = events
        self.renderer = renderer
        self.plex_token_loader = plex_token_loader
        self.immich_settings_loader = immich_settings_loader
        self.progress_persist_interval_seconds = progress_persist_interval_seconds
        self.media_process_gate = media_process_gate or MediaProcessGate()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._next_stale_workdir_reap_at = 0.0

    async def start(self) -> None:
        recovered = await recover_finalizing_jobs(
            self.engine,
            self.run_blocking,
            preserve_workdirs=self.settings.preserve_job_workdirs,
            clip_root=self.settings.resolved_clip_dir,
            work_root=self.settings.resolved_work_dir,
        )
        abandoned = await fail_abandoned_jobs(self.engine)
        for job_id in [*recovered, *abandoned]:
            await self._cleanup_job_workdir(job_id, "application restart")
            await self._publish_durable_job_update(job_id)
        await self._cleanup_stale_job_workdirs()
        self._task = asyncio.create_task(self._run(), name="media-job-runner")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def wake(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        while not self._stopping:
            self._wake.clear()
            if time.monotonic() >= self._next_stale_workdir_reap_at:
                await self._cleanup_stale_job_workdirs()
            try:
                claimed = await claim_next_job(self.engine, f"run-{uuid4()}")
            except Exception:
                logger.exception("The job runner could not claim the next queued job.")
                await asyncio.sleep(1.0)
                continue
            if claimed is None:
                timeout_seconds = max(0.0, self._next_stale_workdir_reap_at - time.monotonic())
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=timeout_seconds)
                continue
            await self._publish_durable_job_update(claimed.id)
            try:
                # Immich uploads are a long-lived network call with no ffmpeg/CPU
                # cost — holding the media-process gate for their duration would
                # stall unrelated interactive media work (e.g. on-demand thumbnail
                # generation) behind them for no reason.
                if claimed.type in ("immich_upload", "bulk_immich_upload"):
                    await self._execute_claimed_job(claimed)
                else:
                    async with self.media_process_gate.slot():
                        await self._execute_claimed_job(claimed)
            except asyncio.CancelledError:
                await self._cleanup_job_workdir(claimed.id, "application shutdown")
                with contextlib.suppress(Exception):
                    await fail_job(
                        self.engine,
                        claimed.id,
                        claimed.run_token,
                        code="APP_SHUTDOWN",
                        message="The application shut down before this job completed.",
                        retryable=True,
                    )
                    await self._publish_durable_job_update(claimed.id)
                raise
            except Exception as error:
                await self._cleanup_job_workdir(claimed.id, "job failure")
                logger.exception("Clip render job %s failed.", claimed.id)
                try:
                    await fail_job(
                        self.engine,
                        claimed.id,
                        claimed.run_token,
                        code=getattr(error, "job_error_code", type(error).__name__.upper()),
                        message=str(error) or "Clip render failed unexpectedly.",
                        retryable=getattr(error, "job_retryable", True),
                        alternatives=getattr(error, "alternatives", None),
                        context=getattr(error, "context", None),
                    )
                except Exception:
                    logger.exception(
                        "The failure state for job %s could not be stored.", claimed.id
                    )
                await self._publish_durable_job_update(claimed.id)

    async def _publish_durable_job_update(self, job_id: str) -> JobSnapshot | None:
        snapshot = await get_job_snapshot(self.engine, job_id)
        await self._publish_job_update(job_id, snapshot)
        return snapshot

    async def _cleanup_job_workdir(self, job_id: str, reason: str) -> None:
        workdir = self.settings.resolved_work_dir / "jobs" / job_id
        if self.settings.preserve_job_workdirs:
            if workdir.exists():
                logger.warning("Preserving media job work directory after %s: %s", reason, workdir)
            return
        await self.run_blocking(_remove_job_workdir, workdir)

    async def _cleanup_stale_job_workdirs(self) -> None:
        self._next_stale_workdir_reap_at = time.monotonic() + STALE_WORKDIR_REAP_INTERVAL_SECONDS
        if self.settings.preserve_job_workdirs:
            return
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            "SELECT id FROM jobs WHERE state IN ('QUEUED', 'RUNNING', 'FINALIZING')"
                        )
                    )
                )
                .mappings()
                .all()
            )
        active_job_ids = {str(row["id"]) for row in rows}
        removed = await self.run_blocking(
            _remove_stale_job_workdirs,
            self.settings.resolved_work_dir / "jobs",
            active_job_ids,
            time.time() - STALE_WORKDIR_AGE_SECONDS,
        )
        if removed:
            noun = "directory" if len(removed) == 1 else "directories"
            logger.info("Removed %s stale media job work %s.", len(removed), noun)

    async def _publish_job_update(self, job_id: str, snapshot: JobSnapshot | None = None) -> None:
        try:
            await self.events.publish(job_id, snapshot)
        except Exception:
            logger.exception("The job runner could not publish an update for job %s.", job_id)

    async def _execute_claimed_job(self, claimed: ClaimedJob) -> None:
        if claimed.type == "thumbnail_generate":
            await self._execute_thumbnail_job(claimed)
            return
        if claimed.type == "clip_metadata_edit":
            await self._execute_metadata_edit_job(claimed)
            return
        if claimed.type == "immich_upload":
            await self._execute_immich_upload_job(claimed)
            return
        if claimed.type == "bulk_immich_upload":
            await self._execute_bulk_immich_upload_job(claimed)
            return
        plan = claimed.render_plan
        if not isinstance(plan, ClipRenderPlan):
            raise TypeError("Clip creation job has an invalid durable plan.")
        await update_running_job(
            self.engine,
            claimed.id,
            claimed.run_token,
            stage="validating",
            progress=0.05,
            current_stage_progress=1,
            message="Source media resolved. Preparing render.",
        )
        await self._publish_durable_job_update(claimed.id)
        last_render_progress_persisted_at = 0.0
        rendering_transition_persisted = False

        async def render_progress(stage_progress: float, message: str) -> None:
            nonlocal latest_snapshot, last_render_progress_persisted_at
            nonlocal rendering_transition_persisted

            progress = 0.05 + stage_progress * 0.9
            now = time.monotonic()
            should_persist = (
                not rendering_transition_persisted
                or stage_progress >= 1.0
                or now - last_render_progress_persisted_at >= self.progress_persist_interval_seconds
            )
            if should_persist:
                await update_running_job(
                    self.engine,
                    claimed.id,
                    claimed.run_token,
                    stage="rendering",
                    progress=progress,
                    current_stage_progress=stage_progress,
                    message=message,
                )
                rendering_transition_persisted = True
                last_render_progress_persisted_at = now
                latest_snapshot = await self._publish_durable_job_update(claimed.id)
                return

            live_snapshot = _live_progress_snapshot(
                latest_snapshot,
                job_id=claimed.id,
                stage="rendering",
                progress=progress,
                current_stage_progress=stage_progress,
                message=message,
            )
            await self._publish_job_update(claimed.id, live_snapshot)

        renderer_settings = self.settings
        if plan.selected_subtitle.strategy == "external_text" and self.plex_token_loader:
            renderer_settings = self.settings.model_copy(
                update={"plex_token": await self.plex_token_loader()}
            )
        rendered = await self.renderer(plan, renderer_settings, progress=render_progress)
        destination = await self.run_blocking(
            resolve_unique_clip_path,
            self.settings.resolved_clip_dir,
            plan.library,
            plan.title,
        )
        rendered_stat = await self.run_blocking(rendered.path.stat)
        clip = _clip_payload(plan, rendered.duration_ms, destination, rendered_stat)

        await transition_to_finalizing(
            self.engine,
            claimed.id,
            claimed.run_token,
            clip_id=plan.clip_id,
            revision=plan.revision,
            destination=destination,
            render_plan_hash=plan.render_plan_hash,
        )
        await create_pending_operation(
            self.engine,
            job_id=claimed.id,
            plan=plan,
            rendered_path=rendered.path,
            destination=destination,
            clip=clip,
        )
        latest_snapshot = await self._publish_durable_job_update(claimed.id)

        await self.run_blocking(
            install_rendered_clip,
            rendered.path,
            destination,
            self.settings.preserve_job_workdirs,
        )
        await insert_clip(self.engine, clip)
        await finish_job_success(self.engine, claimed.id, claimed.run_token, clip=clip)
        await self._publish_durable_job_update(claimed.id)
        installed_stat = await self.run_blocking(destination.stat)
        thumbnail_job = await enqueue_thumbnail_job(
            self.engine,
            build_thumbnail_job_plan(clip, installed_stat),
        )
        await self._publish_durable_job_update(thumbnail_job.id)

        immich_settings = (
            await self.immich_settings_loader() if self.immich_settings_loader else None
        )
        if (
            immich_settings is not None
            and immich_settings.auto_upload
            and immich_settings.url
            and immich_settings.api_key
        ):
            upload_job = await enqueue_immich_upload_job(
                self.engine, build_immich_upload_plan(clip)
            )
            await self._publish_durable_job_update(upload_job.id)

        self.wake()

    async def _execute_thumbnail_job(self, claimed: ClaimedJob) -> None:
        plan = claimed.render_plan
        if not isinstance(plan, ThumbnailJobPlan):
            raise TypeError("Thumbnail job has an invalid durable plan.")
        clip = await get_clip(self.engine, plan.clip_id, self.settings.resolved_clip_dir)
        if clip is None:
            raise ClipRevisionConflict("The clip no longer exists.")
        source = Path(str(clip["file_path"]))
        source_stat = await self.run_blocking(source.stat)
        if (
            int(clip["revision"]) != plan.clip_revision
            or source_stat.st_size != plan.source_size
            or source_stat.st_mtime_ns != plan.source_modified_ns
        ):
            raise ClipRevisionConflict("The clip changed before thumbnail generation began.")
        await update_running_job(
            self.engine,
            claimed.id,
            claimed.run_token,
            stage="generating_thumbnail",
            progress=0.25,
            current_stage_progress=0.25,
            message="Generating clip thumbnail.",
        )
        destination = thumbnail_path(self.settings.resolved_thumbnail_dir, plan.clip_id)
        temp = self.settings.resolved_work_dir / "jobs" / claimed.id / "thumbnail.jpg"
        await generate_thumbnail(
            source,
            temp,
            duration_ms=int(clip["duration_ms"]),
            ffmpeg_path=self.settings.ffmpeg_path,
            timeout_seconds=self.settings.media_preparation_timeout_seconds,
        )
        current_stat = await self.run_blocking(source.stat)
        if (
            current_stat.st_size != plan.source_size
            or current_stat.st_mtime_ns != plan.source_modified_ns
        ):
            raise ClipRevisionConflict("The clip changed while its thumbnail was generated.")
        await self.run_blocking(destination.parent.mkdir, parents=True, exist_ok=True)
        await self.run_blocking(temp.replace, destination)
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    "UPDATE clips SET thumbnail_path = :path, thumbnail_source_size = :size, "
                    "thumbnail_source_modified_ns = :modified "
                    "WHERE id = :id AND revision = :revision"
                ),
                {
                    "path": str(destination),
                    "size": plan.source_size,
                    "modified": plan.source_modified_ns,
                    "id": plan.clip_id,
                    "revision": plan.clip_revision,
                },
            )
            if result.rowcount != 1:
                raise ClipRevisionConflict("The clip changed before its thumbnail was saved.")
        await finish_running_job_success(
            self.engine,
            claimed.id,
            claimed.run_token,
            result_payload={
                "clip_id": plan.clip_id,
                "thumbnail_url": f"/api/clips/{plan.clip_id}/thumbnail",
            },
            message="Thumbnail generation completed.",
        )
        await self._cleanup_job_workdir(claimed.id, "thumbnail completion")
        await self._publish_durable_job_update(claimed.id)

    async def _execute_metadata_edit_job(self, claimed: ClaimedJob) -> None:
        plan = claimed.render_plan
        if not isinstance(plan, MetadataEditJobPlan):
            raise TypeError("Metadata edit job has an invalid durable plan.")
        clip = await get_clip(self.engine, plan.clip_id, self.settings.resolved_clip_dir)
        if clip is None:
            raise ClipRevisionConflict("The clip no longer exists.")
        if int(clip["revision"]) != plan.expected_revision:
            raise ClipRevisionConflict(
                f"Clip revision {plan.expected_revision} is stale; current revision is "
                f"{clip['revision']}."
            )
        await update_running_job(
            self.engine,
            claimed.id,
            claimed.run_token,
            stage="updating_metadata",
            progress=0.2,
            current_stage_progress=0.2,
            message="Writing the next clip metadata revision.",
        )
        source = Path(str(clip["file_path"]))
        temp = self.settings.resolved_work_dir / "jobs" / claimed.id / "metadata.mp4"
        proposed = {**clip, **plan.proposed}
        destination = await self.run_blocking(
            resolve_unique_clip_path,
            self.settings.resolved_clip_dir,
            str(proposed["library"]),
            str(proposed["title"]),
            exclude=source,
        )
        paths_are_safe = await self.run_blocking(
            _metadata_paths_are_safe,
            source,
            destination,
            temp,
            self.settings.resolved_clip_dir,
            self.settings.resolved_work_dir,
        )
        if not paths_are_safe:
            raise ValueError("Metadata file operation escaped a managed directory.")
        proposed["updated_at"] = utc_now()
        proposed["file_path"] = str(destination)
        proposed["thumbnail_path"] = None
        proposed["thumbnail_source_size"] = None
        proposed["thumbnail_source_modified_ns"] = None
        await rewrite_clip_metadata(
            source,
            temp,
            proposed,
            ffmpeg_path=self.settings.ffmpeg_path,
            timeout_seconds=self.settings.media_preparation_timeout_seconds,
        )
        temp_stat = await self.run_blocking(temp.stat)
        proposed["file_size_bytes"] = temp_stat.st_size
        proposed["file_modified_ns"] = temp_stat.st_mtime_ns
        latest = await get_clip(self.engine, plan.clip_id, self.settings.resolved_clip_dir)
        if latest is None or int(latest["revision"]) != plan.expected_revision:
            raise ClipRevisionConflict("The clip changed while metadata was being written.")
        await transition_to_finalizing(
            self.engine,
            claimed.id,
            claimed.run_token,
            clip_id=plan.clip_id,
            revision=int(proposed["revision"]),
            destination=destination,
            render_plan_hash=plan.operation_hash,
        )
        await create_pending_metadata_operation(
            self.engine,
            job_id=claimed.id,
            clip_id=plan.clip_id,
            temp_path=temp,
            source_path=source,
            destination=destination,
            expected_revision=plan.expected_revision,
            operation_hash=plan.operation_hash,
            clip=proposed,
        )
        await self._publish_durable_job_update(claimed.id)
        await self.run_blocking(install_metadata_revision, temp, source, destination)
        installed_stat = await self.run_blocking(destination.stat)
        proposed["file_size_bytes"] = installed_stat.st_size
        proposed["file_modified_ns"] = installed_stat.st_mtime_ns
        await commit_metadata_edit(
            self.engine, proposed, expected_revision=plan.expected_revision
        )
        await self.run_blocking(remove_superseded_clip, source, destination)
        await finish_job_success(
            self.engine,
            claimed.id,
            claimed.run_token,
            clip=proposed,
            message="Clip metadata update completed.",
        )
        await self._cleanup_job_workdir(claimed.id, "metadata update completion")
        await self._publish_durable_job_update(claimed.id)
        thumbnail_job = await enqueue_thumbnail_job(
            self.engine,
            build_thumbnail_job_plan(proposed, installed_stat),
        )
        await self._publish_durable_job_update(thumbnail_job.id)
        self.wake()


    async def _upload_and_organize_clip(
        self,
        clip: dict[str, Any],
        immich_settings: ImmichJobSettings,
        *,
        report_stage: Callable[[JobStage, float, str], Awaitable[None]],
    ) -> ImmichOrganizeResult:
        """Upload (or reuse) a clip's Immich asset, then set its description and
        apply its tags. Shared by the single-clip job and the bulk job so this
        logic — including all of its durability/idempotency guarantees — exists
        exactly once.
        """
        immich_url = immich_settings.url
        immich_api_key = immich_settings.api_key
        if not immich_url or not immich_api_key:
            raise _ImmichNotConfiguredError("Immich is not configured.")
        clip_id = str(clip["id"])

        await report_stage("uploading_asset", 0.1, "Uploading clip to Immich.")

        normalized_url = normalize_immich_url(immich_url)
        reusing = (
            bool(clip.get("immich_asset_id")) and clip.get("immich_server_url") == normalized_url
        )

        if reusing:
            asset_id = str(clip["immich_asset_id"])
        else:
            source = Path(str(clip["file_path"]))
            source_stat = await self.run_blocking(source.stat)
            recorded_size = clip.get("file_size_bytes")
            recorded_modified_ns = clip.get("file_modified_ns")
            # Older clips (recorded before these columns existed, or inserted
            # through a path that never populated them) have no fingerprint to
            # compare against — nothing to detect drift against, so there's
            # nothing to reject. Only compare when both are actually recorded.
            if (
                recorded_size is not None
                and recorded_modified_ns is not None
                and (
                    source_stat.st_size != recorded_size
                    or source_stat.st_mtime_ns != recorded_modified_ns
                )
            ):
                raise ClipRevisionConflict(
                    "The clip file changed before it could be uploaded to Immich."
                )
            asset_id = await self.run_blocking(
                upload_immich_asset_sync,
                source,
                normalized_url,
                immich_api_key,
                file_created_at=_as_utc_datetime(clip["created_at"]),
                file_modified_at=datetime.fromtimestamp(source_stat.st_mtime, tz=UTC),
            )
            try:
                await set_clip_immich_asset_id(self.engine, clip_id, asset_id, normalized_url)
            except Exception as error:
                return ImmichOrganizeResult(
                    asset_id=asset_id,
                    state="PARTIAL",
                    error=JobError(
                        code=getattr(error, "job_error_code", "IMMICH_ASSET_ASSOCIATION_FAILED"),
                        message=str(error),
                        retryable=getattr(error, "job_retryable", False),
                    ),
                    message="Uploaded to Immich, but the local association could not be recorded.",
                    result_payload={
                        "clip_id": clip_id,
                        "immich_asset_id": asset_id,
                        "description_set": False,
                        "tags_applied": [],
                    },
                )

        await report_stage("setting_description", 0.6, "Setting the Immich asset description.")

        # Description and tagging are independent optional steps — neither
        # short-circuits the other, so a failure in one never hides a success in
        # the other. The one exception: a confirmed-missing asset on the reuse
        # path means nothing further is worth attempting against it.
        description_error: JobError | None = None
        try:
            await set_immich_asset_description(
                asset_id, str(clip["title"]), normalized_url, immich_api_key
            )
        except ImmichApiError as error:
            if reusing and isinstance(error, ImmichAssetNotFoundError):
                # Nothing new was created this run — a PARTIAL result would wrongly
                # imply a confirmed asset. The stale `immich_asset_id` is left as-is;
                # relinking it is a P4-05 concern.
                return ImmichOrganizeResult(
                    asset_id=asset_id,
                    state="FAILED",
                    error=JobError(
                        code=error.job_error_code,
                        message=str(error),
                        retryable=error.job_retryable,
                    ),
                    message=str(error),
                    result_payload={
                        "clip_id": clip_id,
                        "immich_asset_id": asset_id,
                        "description_set": False,
                        "tags_applied": [],
                    },
                )
            description_error = JobError(
                code=error.job_error_code, message=str(error), retryable=error.job_retryable
            )

        await report_stage("applying_tags", 0.85, "Applying Immich tags.")

        tag_paths = build_immich_tag_paths(
            clip,
            default_tag=immich_settings.default_tag,
            tag_library=immich_settings.tag_library,
            tag_show=immich_settings.tag_show,
            tag_episode=immich_settings.tag_episode,
        )
        # Stored tag ids belong to whatever server they were last applied on. When
        # this run uploaded fresh (a new asset, possibly on a different server —
        # see `reusing` above), those ids are foreign to it: sending them to
        # `untag_immich_assets` against a server that never issued them would
        # fail (or worse, silently target an unrelated tag that happens to share
        # the id). Only trust the cache when we're continuing against the same
        # asset+server association it was recorded for.
        previous_tag_ids = (
            parse_stored_immich_tag_ids(clip.get("immich_tag_ids")) if reusing else []
        )
        # What we believe is actually applied right now — seeded from the durable
        # record and updated as each add/remove call succeeds, so a failure partway
        # through still leaves an accurate record for the next run to diff against,
        # rather than an all-or-nothing guess.
        applied_tag_ids = list(previous_tag_ids)
        tags_applied: list[str] = []
        tag_error: JobError | None = None
        if tag_paths or previous_tag_ids:
            try:
                new_tag_ids: list[str] = []
                if tag_paths:
                    upserted = await upsert_immich_tags(
                        tag_paths, normalized_url, immich_api_key
                    )
                    new_tag_ids = [upserted[path] for path in tag_paths if path in upserted]
                    if new_tag_ids:
                        await tag_immich_assets(
                            asset_id, new_tag_ids, normalized_url, immich_api_key
                        )
                        applied_tag_ids = list(dict.fromkeys([*applied_tag_ids, *new_tag_ids]))
                        tags_applied = tag_paths
                # Anything previously applied that the current configuration no
                # longer resolves to (a renamed library/show, a disabled toggle,
                # a cleared default tag) is stale — remove it rather than leaving
                # it to accumulate forever.
                for stale_tag_id in previous_tag_ids:
                    if stale_tag_id in new_tag_ids:
                        continue
                    await untag_immich_assets(
                        stale_tag_id, [asset_id], normalized_url, immich_api_key
                    )
                    applied_tag_ids = [tid for tid in applied_tag_ids if tid != stale_tag_id]
            except ImmichApiError as error:
                tag_error = JobError(
                    code=error.job_error_code, message=str(error), retryable=error.job_retryable
                )
            finally:
                if applied_tag_ids != previous_tag_ids:
                    await set_clip_immich_tag_ids(self.engine, clip_id, applied_tag_ids)

        result_payload = {
            "clip_id": clip_id,
            "immich_asset_id": asset_id,
            "description_set": description_error is None,
            "tags_applied": tags_applied,
        }

        if description_error is not None and tag_error is not None:
            failure: JobError | None = JobError(
                code="IMMICH_ORGANIZE_FAILED",
                message=(
                    f"Description: {description_error.message} Tagging: {tag_error.message}"
                ),
                retryable=description_error.retryable or tag_error.retryable,
            )
            message = "Uploaded to Immich, but the description and tags could not be applied."
        elif description_error is not None:
            failure = description_error
            message = "Uploaded to Immich, but the description could not be set."
        elif tag_error is not None:
            failure = tag_error
            message = "Uploaded to Immich, but the tags could not be applied."
        else:
            failure = None
            message = "Clip uploaded and organized in Immich."

        return ImmichOrganizeResult(
            asset_id=asset_id,
            state="PARTIAL" if failure is not None else "SUCCEEDED",
            error=failure,
            message=message,
            result_payload=result_payload,
        )

    async def _execute_immich_upload_job(self, claimed: ClaimedJob) -> None:
        plan = claimed.render_plan
        if not isinstance(plan, ImmichUploadJobPlan):
            raise TypeError("Immich upload job has an invalid durable plan.")
        clip = await get_clip(self.engine, plan.clip_id, self.settings.resolved_clip_dir)
        if clip is None:
            raise ClipRevisionConflict("The clip no longer exists.")
        immich_settings = (
            await self.immich_settings_loader() if self.immich_settings_loader else None
        )
        if immich_settings is None or not immich_settings.url or not immich_settings.api_key:
            raise _ImmichNotConfiguredError("Immich is not configured.")

        async def report_stage(stage: JobStage, progress: float, message: str) -> None:
            await update_running_job(
                self.engine,
                claimed.id,
                claimed.run_token,
                stage=stage,
                progress=progress,
                current_stage_progress=progress,
                message=message,
            )
            await self._publish_durable_job_update(claimed.id)

        result = await self._upload_and_organize_clip(
            clip, immich_settings, report_stage=report_stage
        )

        if result.state == "FAILED":
            error = result.error or JobError(code="IMMICH_ORGANIZE_FAILED", message=result.message)
            await fail_job(
                self.engine,
                claimed.id,
                claimed.run_token,
                code=error.code,
                message=error.message,
                retryable=error.retryable,
            )
        elif result.state == "PARTIAL":
            await finish_running_job_partial(
                self.engine,
                claimed.id,
                claimed.run_token,
                result_payload=result.result_payload,
                error=result.error
                or JobError(code="IMMICH_ORGANIZE_FAILED", message=result.message),
                message=result.message,
            )
        else:
            await finish_running_job_success(
                self.engine,
                claimed.id,
                claimed.run_token,
                result_payload=result.result_payload,
                message=result.message,
            )
        await self._publish_durable_job_update(claimed.id)

    async def _execute_bulk_immich_upload_job(self, claimed: ClaimedJob) -> None:
        plan = claimed.render_plan
        if not isinstance(plan, BulkImmichUploadJobPlan):
            raise TypeError("Bulk Immich upload job has an invalid durable plan.")
        immich_settings = (
            await self.immich_settings_loader() if self.immich_settings_loader else None
        )
        if immich_settings is None or not immich_settings.url or not immich_settings.api_key:
            raise _ImmichNotConfiguredError("Immich is not configured.")
        normalized_url = normalize_immich_url(immich_settings.url)

        async def noop_report_stage(_stage: JobStage, _progress: float, _message: str) -> None:
            return None

        total = len(plan.clip_ids)
        succeeded = partial = failed = skipped = 0
        details: list[dict[str, Any]] = []

        for index, clip_id in enumerate(plan.clip_ids):
            await update_running_job(
                self.engine,
                claimed.id,
                claimed.run_token,
                stage="uploading_asset",
                progress=(index / total) if total else 1.0,
                current_stage_progress=0.0,
                message=f"Processing clip {index + 1} of {total}.",
            )
            await self._publish_durable_job_update(claimed.id)

            clip = await get_clip(self.engine, clip_id, self.settings.resolved_clip_dir)
            if clip is None:
                skipped += 1
                details.append(
                    {"clip_id": clip_id, "title": None, "outcome": "skipped", "error_code": None}
                )
                continue

            already_linked = (
                bool(clip.get("immich_asset_id"))
                and clip.get("immich_server_url") == normalized_url
            )
            if already_linked:
                skipped += 1
                details.append(
                    {
                        "clip_id": clip_id,
                        "title": str(clip["title"]),
                        "outcome": "skipped",
                        "error_code": None,
                    }
                )
                continue

            try:
                result = await self._upload_and_organize_clip(
                    clip, immich_settings, report_stage=noop_report_stage
                )
            except Exception as error:
                failed += 1
                details.append(
                    {
                        "clip_id": clip_id,
                        "title": str(clip.get("title") or clip_id),
                        "outcome": "failed",
                        "error_code": getattr(
                            error, "job_error_code", type(error).__name__.upper()
                        ),
                    }
                )
                continue

            if result.state == "SUCCEEDED":
                succeeded += 1
            elif result.state == "PARTIAL":
                partial += 1
            else:
                failed += 1
            details.append(
                {
                    "clip_id": clip_id,
                    "title": str(clip["title"]),
                    "outcome": result.state.lower(),
                    "error_code": result.error.code if result.error else None,
                }
            )

        attempted = succeeded + partial + failed
        result_payload = {
            "total": total,
            "succeeded": succeeded,
            "partial": partial,
            "failed": failed,
            "skipped": skipped,
            "details": details,
        }
        summary_message = (
            f"Bulk upload complete: {succeeded} succeeded, {partial} partial, "
            f"{failed} failed, {skipped} skipped."
        )

        if attempted == 0 or (partial == 0 and failed == 0):
            await finish_running_job_success(
                self.engine,
                claimed.id,
                claimed.run_token,
                result_payload=result_payload,
                message=summary_message,
            )
        elif failed == attempted:
            await fail_job(
                self.engine,
                claimed.id,
                claimed.run_token,
                code="BULK_UPLOAD_FAILED",
                message=summary_message,
                retryable=True,
            )
        else:
            await finish_running_job_partial(
                self.engine,
                claimed.id,
                claimed.run_token,
                result_payload=result_payload,
                error=JobError(code="BULK_UPLOAD_PARTIAL", message=summary_message, retryable=True),
                message=summary_message,
            )
        await self._publish_durable_job_update(claimed.id)


def _metadata_paths_are_safe(
    source: Path,
    destination: Path,
    temp: Path,
    clip_root: Path,
    work_root: Path,
) -> bool:
    resolved_clips = clip_root.resolve(strict=False)
    return (
        source.resolve(strict=False).is_relative_to(resolved_clips)
        and destination.resolve(strict=False).is_relative_to(resolved_clips)
        and temp.resolve(strict=False).is_relative_to(work_root.resolve(strict=False))
    )


def _clip_payload(
    plan: ClipRenderPlan,
    rendered_duration_ms: int,
    destination: Path,
    rendered_stat: Any | None = None,
) -> dict[str, Any]:
    now = utc_now()
    return {
        "id": plan.clip_id,
        "title": plan.title,
        "library": plan.library,
        "media_type": plan.media_type,
        "custom_title": plan.custom_title,
        "automatic_title": plan.automatic_title or plan.title,
        "movie_title": plan.movie_title,
        "movie_year": plan.movie_year,
        "show_name": plan.show_name,
        "episode_title": plan.episode_title,
        "season_number": plan.season_number,
        "episode_number": plan.episode_number,
        "clip_number": plan.clip_number,
        "plex_username": plan.plex_user,
        "file_path": str(destination),
        "duration_ms": rendered_duration_ms,
        "revision": plan.revision,
        "source_start_ms": plan.source_start_ms,
        "source_end_ms": plan.source_end_ms,
        "source_path": plan.source_media.local_path,
        "source_size_bytes": plan.source_media.fingerprint.size_bytes,
        "source_modified_at": plan.source_media.fingerprint.modified_at.replace(tzinfo=None),
        "selected_audio_stream_index": plan.selected_audio_stream.stream_index,
        "render_plan_hash": plan.render_plan_hash,
        "file_size_bytes": rendered_stat.st_size if rendered_stat is not None else None,
        "file_modified_ns": rendered_stat.st_mtime_ns if rendered_stat is not None else None,
        "created_at": now,
        "updated_at": now,
    }
