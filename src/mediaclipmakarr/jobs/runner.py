"""Sequential background-job execution orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mediaclipmakarr.clips import insert_clip
from mediaclipmakarr.config import Settings
from mediaclipmakarr.media_renderer import RenderedClipFile, render_clip_file
from mediaclipmakarr.render_plan import ClipRenderPlan, resolve_unique_clip_path

from .events import JobEventBroker
from .finalization import install_rendered_clip
from .models import BlockingRunner, ClaimedJob, JobSnapshot
from .recovery import fail_abandoned_jobs, recover_finalizing_jobs
from .repository import (
    _live_progress_snapshot,
    claim_next_job,
    create_pending_operation,
    fail_job,
    finish_job_success,
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
        progress_persist_interval_seconds: float = 1.0,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.run_blocking = run_blocking
        self.events = events
        self.renderer = renderer
        self.plex_token_loader = plex_token_loader
        self.progress_persist_interval_seconds = progress_persist_interval_seconds
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._next_stale_workdir_reap_at = 0.0

    async def start(self) -> None:
        recovered = await recover_finalizing_jobs(
            self.engine,
            self.run_blocking,
            preserve_workdirs=self.settings.preserve_job_workdirs,
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
                await connection.execute(
                    text("SELECT id FROM jobs WHERE state IN ('QUEUED', 'RUNNING', 'FINALIZING')")
                )
            ).mappings().all()
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

    async def _publish_job_update(
        self, job_id: str, snapshot: JobSnapshot | None = None
    ) -> None:
        try:
            await self.events.publish(job_id, snapshot)
        except Exception:
            logger.exception("The job runner could not publish an update for job %s.", job_id)

    async def _execute_claimed_job(self, claimed: ClaimedJob) -> None:
        plan = claimed.render_plan
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
                or now - last_render_progress_persisted_at
                >= self.progress_persist_interval_seconds
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
        clip = _clip_payload(plan, rendered.duration_ms, destination)

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

def _clip_payload(
    plan: ClipRenderPlan, rendered_duration_ms: int, destination: Path
) -> dict[str, Any]:
    now = utc_now()
    return {
        "id": plan.clip_id,
        "title": plan.title,
        "library": plan.library,
        "media_type": plan.media_type,
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
        "created_at": now,
        "updated_at": now,
    }
