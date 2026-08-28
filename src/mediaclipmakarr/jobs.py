from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mediaclipmakarr.config import Settings
from mediaclipmakarr.media_renderer import RenderedClipFile, render_clip_file
from mediaclipmakarr.render_plan import ClipRenderPlan, resolve_unique_clip_path

logger = logging.getLogger(__name__)

JobState = Literal["QUEUED", "RUNNING", "FINALIZING", "SUCCEEDED", "PARTIAL", "FAILED"]
JobType = Literal["clip_create"]
JobStage = Literal["queued", "validating", "rendering", "finalizing", "complete", "failed"]

BlockingRunner = Callable[..., Awaitable[Any]]
ClipRenderer = Callable[..., Awaitable[RenderedClipFile]]


class JobError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class JobSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: JobType
    state: JobState
    stage: JobStage
    progress: float
    current_stage_progress: float
    elapsed_ms: int | None
    queue_position: int | None
    message: str
    result: dict[str, Any] | None = None
    error: JobError | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: str
    run_token: str
    render_plan: ClipRenderPlan


class JobUpdateConflict(RuntimeError):
    pass


class JobEventBroker:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._versions: dict[str, int] = {}
        self._snapshots: dict[str, JobSnapshot] = {}

    def version(self, job_id: str) -> int:
        return self._versions.get(job_id, 0)

    def snapshot(self, job_id: str) -> JobSnapshot | None:
        return self._snapshots.get(job_id)

    async def publish(self, job_id: str, snapshot: JobSnapshot | None = None) -> None:
        async with self._condition:
            if snapshot is not None:
                self._snapshots[job_id] = snapshot
            self._versions[job_id] = self.version(job_id) + 1
            self._condition.notify_all()

    async def wait_for_change(
        self, job_id: str, version: int, *, timeout_seconds: float
    ) -> tuple[int, bool]:
        async with self._condition:
            if self.version(job_id) == version:
                try:
                    await asyncio.wait_for(
                        self._condition.wait_for(lambda: self.version(job_id) != version),
                        timeout_seconds,
                    )
                except TimeoutError:
                    return self.version(job_id), False
            return self.version(job_id), True


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def enqueue_clip_create_job(engine: AsyncEngine, plan: ClipRenderPlan) -> JobSnapshot:
    created_at = utc_now()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs "
                "(id, type, state, stage, progress, current_stage_progress, message, attempt, "
                "render_plan_json, render_plan_hash, created_at) "
                "VALUES (:id, 'clip_create', 'QUEUED', 'queued', 0, 0, :message, 0, "
                ":render_plan_json, :render_plan_hash, :created_at)"
            ),
            {
                "id": plan.job_id,
                "message": "Clip render is queued.",
                "render_plan_json": _dump_json(plan.model_dump(mode="json")),
                "render_plan_hash": plan.render_plan_hash,
                "created_at": created_at,
            },
        )
    snapshot = await get_job_snapshot(engine, plan.job_id)
    if snapshot is None:
        raise RuntimeError("The queued job could not be read back from SQLite.")
    return snapshot


async def get_job_snapshot(engine: AsyncEngine, job_id: str) -> JobSnapshot | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id})
        ).mappings().first()
        if row is None:
            return None
        queue_position = None
        if row["state"] == "QUEUED":
            queue_position = await connection.scalar(
                text(
                    "SELECT COUNT(*) FROM jobs "
                    "WHERE state = 'QUEUED' AND created_at <= :created_at"
                ),
                {"created_at": row["created_at"]},
            )
    return _snapshot_from_row(dict(row), queue_position=int(queue_position or 0) or None)


async def claim_next_job(engine: AsyncEngine, run_token: str) -> ClaimedJob | None:
    started_at = utc_now()
    async with engine.begin() as connection:
        candidate = (
            await connection.execute(
                text(
                    "SELECT id, render_plan_json FROM jobs "
                    "WHERE state = 'QUEUED' ORDER BY created_at LIMIT 1"
                )
            )
        ).mappings().first()
        if candidate is None:
            return None
        result = await connection.execute(
            text(
                "UPDATE jobs SET state = 'RUNNING', stage = 'validating', progress = 0, "
                "current_stage_progress = 0, started_at = :started_at, run_token = :run_token, "
                "attempt = attempt + 1, message = :message "
                "WHERE id = :id AND state = 'QUEUED'"
            ),
            {
                "id": candidate["id"],
                "run_token": run_token,
                "started_at": started_at,
                "message": "Clip render job claimed.",
            },
        )
        if result.rowcount != 1:
            return None

    return ClaimedJob(
        id=str(candidate["id"]),
        run_token=run_token,
        render_plan=ClipRenderPlan.model_validate_json(str(candidate["render_plan_json"])),
    )


async def fail_abandoned_jobs(engine: AsyncEngine) -> list[str]:
    finished_at = utc_now()
    error = JobError(
        code="APP_RESTARTED",
        message="The application restarted before this job completed.",
        retryable=True,
    )
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                text("SELECT id FROM jobs WHERE state IN ('RUNNING', 'FINALIZING')")
            )
        ).mappings().all()
        ids = [str(row["id"]) for row in rows]
        if ids:
            await connection.execute(
                text(
                    "UPDATE jobs SET state = 'FAILED', stage = 'failed', progress = 1, "
                    "current_stage_progress = 1, finished_at = :finished_at, run_token = NULL, "
                    "error_json = :error_json, message = :message "
                    "WHERE state IN ('RUNNING', 'FINALIZING')"
                ),
                {
                    "finished_at": finished_at,
                    "error_json": _dump_json(error.model_dump(mode="json")),
                    "message": error.message,
                },
            )
    return ids


async def recover_finalizing_jobs(engine: AsyncEngine, run_blocking: BlockingRunner) -> list[str]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT job_id, temp_path, target_path, clip_json "
                    "FROM pending_file_operations "
                    "WHERE job_id IN (SELECT id FROM jobs WHERE state = 'FINALIZING')"
                )
            )
        ).mappings().all()

    recovered: list[str] = []
    for row in rows:
        job_id = str(row["job_id"])
        clip = _load_json(row["clip_json"])
        if clip is None:
            await fail_job(
                engine,
                job_id,
                None,
                code="FINALIZATION_RECOVERY_FAILED",
                message="The pending clip metadata could not be recovered after restart.",
            )
            recovered.append(job_id)
            continue

        installed = await run_blocking(
            _recover_pending_installation,
            Path(str(row["temp_path"])),
            Path(str(row["target_path"])),
        )
        if not installed:
            await fail_job(
                engine,
                job_id,
                None,
                code="APP_RESTARTED",
                message="The application restarted before finalizing the rendered clip.",
                retryable=True,
            )
            recovered.append(job_id)
            continue

        await insert_clip_if_missing(engine, clip)
        await finish_job_success_without_token(engine, job_id, clip=clip)
        recovered.append(job_id)
    return recovered


async def update_running_job(
    engine: AsyncEngine,
    job_id: str,
    run_token: str,
    *,
    stage: JobStage,
    progress: float,
    current_stage_progress: float,
    message: str,
    expected_state: JobState = "RUNNING",
) -> None:
    result = await _guarded_update(
        engine,
        job_id,
        run_token,
        expected_state,
        {
            "stage": stage,
            "progress": _clamp(progress),
            "current_stage_progress": _clamp(current_stage_progress),
            "message": message,
        },
    )
    if result != 1:
        raise JobUpdateConflict(f"Job {job_id} was changed by another runner.")


async def transition_to_finalizing(
    engine: AsyncEngine,
    job_id: str,
    run_token: str,
    *,
    clip_id: str,
    revision: int,
    destination: Path,
    render_plan_hash: str,
) -> None:
    result = await _guarded_update(
        engine,
        job_id,
        run_token,
        "RUNNING",
        {
            "state": "FINALIZING",
            "stage": "finalizing",
            "progress": 0.95,
            "current_stage_progress": 0,
            "message": "Installing rendered clip.",
            "finalizing_clip_id": clip_id,
            "finalizing_revision": revision,
            "finalizing_destination": str(destination),
            "render_plan_hash": render_plan_hash,
        },
    )
    if result != 1:
        raise JobUpdateConflict(f"Job {job_id} could not enter finalization.")


async def finish_job_success(
    engine: AsyncEngine,
    job_id: str,
    run_token: str,
    *,
    clip: dict[str, Any],
) -> None:
    finished_at = utc_now()
    result_payload = {
        "clip_id": clip["id"],
        "title": clip["title"],
        "file_path": clip["file_path"],
        "duration_ms": clip["duration_ms"],
        "play_url": f"/api/clips/{clip['id']}/media",
        "download_url": f"/api/clips/{clip['id']}/download",
    }
    async with engine.begin() as connection:
        result = await connection.execute(
            text(
                "UPDATE jobs SET state = 'SUCCEEDED', stage = 'complete', progress = 1, "
                "current_stage_progress = 1, finished_at = :finished_at, run_token = NULL, "
                "result_json = :result_json, message = :message "
                "WHERE id = :id AND state = 'FINALIZING' AND run_token = :run_token"
            ),
            {
                "id": job_id,
                "run_token": run_token,
                "finished_at": finished_at,
                "result_json": _dump_json(result_payload),
                "message": "Clip render completed.",
            },
        )
        if result.rowcount != 1:
            raise JobUpdateConflict(f"Job {job_id} could not be completed.")
        await connection.execute(
            text("DELETE FROM pending_file_operations WHERE job_id = :job_id"),
            {"job_id": job_id},
        )


async def finish_job_success_without_token(
    engine: AsyncEngine,
    job_id: str,
    *,
    clip: dict[str, Any],
) -> None:
    finished_at = utc_now()
    result_payload = {
        "clip_id": clip["id"],
        "title": clip["title"],
        "file_path": clip["file_path"],
        "duration_ms": clip["duration_ms"],
        "play_url": f"/api/clips/{clip['id']}/media",
        "download_url": f"/api/clips/{clip['id']}/download",
    }
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE jobs SET state = 'SUCCEEDED', stage = 'complete', progress = 1, "
                "current_stage_progress = 1, finished_at = :finished_at, run_token = NULL, "
                "result_json = :result_json, error_json = NULL, message = :message "
                "WHERE id = :id AND state = 'FINALIZING'"
            ),
            {
                "id": job_id,
                "finished_at": finished_at,
                "result_json": _dump_json(result_payload),
                "message": "Clip finalization recovered after application restart.",
            },
        )
        await connection.execute(
            text("DELETE FROM pending_file_operations WHERE job_id = :job_id"),
            {"job_id": job_id},
        )


async def fail_job(
    engine: AsyncEngine,
    job_id: str,
    run_token: str | None,
    *,
    code: str,
    message: str,
    retryable: bool = False,
) -> None:
    finished_at = utc_now()
    error = JobError(code=code, message=message, retryable=retryable)
    token_clause = "AND run_token = :run_token" if run_token else ""
    await _execute_update(
        engine,
        (
            "UPDATE jobs SET state = 'FAILED', stage = 'failed', progress = 1, "
            "current_stage_progress = 1, finished_at = :finished_at, run_token = NULL, "
            "error_json = :error_json, message = :message "
            f"WHERE id = :id AND state IN ('QUEUED', 'RUNNING', 'FINALIZING') {token_clause}"
        ),
        {
            "id": job_id,
            "run_token": run_token,
            "finished_at": finished_at,
            "error_json": _dump_json(error.model_dump(mode="json")),
            "message": message,
        },
    )


async def get_clip(engine: AsyncEngine, clip_id: str, clip_root: Path) -> dict[str, Any] | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(text("SELECT * FROM clips WHERE id = :id"), {"id": clip_id})
        ).mappings().first()
    if row is None:
        return None
    clip = dict(row)
    return _clip_with_safe_path(clip, clip_root)


def _clip_with_safe_path(clip: dict[str, Any], clip_root: Path) -> dict[str, Any] | None:
    path = Path(str(clip["file_path"])).resolve(strict=False)
    if not path.is_relative_to(clip_root.resolve(strict=False)):
        return None
    clip["file_path"] = str(path)
    return clip


class JobRunner:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: Settings,
        *,
        run_blocking: BlockingRunner,
        events: JobEventBroker,
        renderer: ClipRenderer = render_clip_file,
        progress_persist_interval_seconds: float = 1.0,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.run_blocking = run_blocking
        self.events = events
        self.renderer = renderer
        self.progress_persist_interval_seconds = progress_persist_interval_seconds
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        recovered = await recover_finalizing_jobs(self.engine, self.run_blocking)
        abandoned = await fail_abandoned_jobs(self.engine)
        for job_id in [*recovered, *abandoned]:
            await self._publish_durable_job_update(job_id)
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
            try:
                claimed = await claim_next_job(self.engine, f"run-{uuid4()}")
            except Exception:
                logger.exception("The job runner could not claim the next queued job.")
                await asyncio.sleep(1.0)
                continue
            if claimed is None:
                await self._wake.wait()
                continue
            await self._publish_durable_job_update(claimed.id)
            try:
                await self._execute_claimed_job(claimed)
            except asyncio.CancelledError:
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
                logger.exception("Clip render job %s failed.", claimed.id)
                try:
                    await fail_job(
                        self.engine,
                        claimed.id,
                        claimed.run_token,
                        code=type(error).__name__.upper(),
                        message=str(error) or "Clip render failed unexpectedly.",
                        retryable=True,
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

        rendered = await self.renderer(plan, self.settings, progress=render_progress)
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

        await self.run_blocking(_install_rendered_clip, rendered.path, destination)
        await insert_clip(self.engine, clip)
        await finish_job_success(self.engine, claimed.id, claimed.run_token, clip=clip)
        await self._publish_durable_job_update(claimed.id)


async def create_pending_operation(
    engine: AsyncEngine,
    *,
    job_id: str,
    plan: ClipRenderPlan,
    rendered_path: Path,
    destination: Path,
    clip: dict[str, Any],
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO pending_file_operations "
                "(id, job_id, clip_id, operation_type, temp_path, target_path, "
                "expected_revision, render_plan_hash, clip_json, created_at) "
                "VALUES (:id, :job_id, :clip_id, 'create_clip', :temp_path, :target_path, "
                ":expected_revision, :render_plan_hash, :clip_json, :created_at)"
            ),
            {
                "id": f"pending-{uuid4()}",
                "job_id": job_id,
                "clip_id": plan.clip_id,
                "temp_path": str(rendered_path),
                "target_path": str(destination),
                "expected_revision": plan.revision,
                "render_plan_hash": plan.render_plan_hash,
                "clip_json": _dump_json(clip),
                "created_at": utc_now(),
            },
        )


async def insert_clip(engine: AsyncEngine, clip: dict[str, Any]) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO clips "
                "(id, title, library, media_type, file_path, duration_ms, revision, "
                "source_start_ms, source_end_ms, source_path, source_size_bytes, "
                "source_modified_at, selected_audio_stream_index, render_plan_hash, "
                "created_at, updated_at) "
                "VALUES (:id, :title, :library, :media_type, :file_path, :duration_ms, "
                ":revision, :source_start_ms, :source_end_ms, :source_path, "
                ":source_size_bytes, :source_modified_at, :selected_audio_stream_index, "
                ":render_plan_hash, :created_at, :updated_at)"
            ),
            clip,
        )


async def insert_clip_if_missing(engine: AsyncEngine, clip: dict[str, Any]) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT OR IGNORE INTO clips "
                "(id, title, library, media_type, file_path, duration_ms, revision, "
                "source_start_ms, source_end_ms, source_path, source_size_bytes, "
                "source_modified_at, selected_audio_stream_index, render_plan_hash, "
                "created_at, updated_at) "
                "VALUES (:id, :title, :library, :media_type, :file_path, :duration_ms, "
                ":revision, :source_start_ms, :source_end_ms, :source_path, "
                ":source_size_bytes, :source_modified_at, :selected_audio_stream_index, "
                ":render_plan_hash, :created_at, :updated_at)"
            ),
            clip,
        )


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


def _install_rendered_clip(temp_path: Path, destination: Path) -> None:
    destination = destination.resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError("The resolved clip destination already exists.")
    temp_path.replace(destination)
    shutil.rmtree(temp_path.parent, ignore_errors=True)


def _recover_pending_installation(temp_path: Path, destination: Path) -> bool:
    destination = destination.resolve(strict=False)
    if destination.exists():
        shutil.rmtree(temp_path.parent, ignore_errors=True)
        return True
    if not temp_path.exists():
        return False
    _install_rendered_clip(temp_path, destination)
    return True


async def _guarded_update(
    engine: AsyncEngine,
    job_id: str,
    run_token: str,
    expected_state: JobState,
    fields: dict[str, Any],
) -> int:
    assignments = ", ".join(f"{field} = :{field}" for field in fields)
    values = {"id": job_id, "run_token": run_token, **fields}
    return await _execute_update(
        engine,
        (
            f"UPDATE jobs SET {assignments} "
            "WHERE id = :id AND state = :expected_state AND run_token = :run_token"
        ),
        {**values, "expected_state": expected_state},
    )


async def _execute_update(engine: AsyncEngine, statement: str, values: dict[str, Any]) -> int:
    async with engine.begin() as connection:
        result = await connection.execute(text(statement), values)
        return int(result.rowcount or 0)


def _snapshot_from_row(row: dict[str, Any], *, queue_position: int | None) -> JobSnapshot:
    started_at = _datetime_or_none(row.get("started_at"))
    finished_at = _datetime_or_none(row.get("finished_at"))
    elapsed_ms = None
    if started_at:
        end = finished_at or utc_now()
        elapsed_ms = max(0, round((end - started_at).total_seconds() * 1000))
    return JobSnapshot(
        id=str(row["id"]),
        type=row["type"],
        state=row["state"],
        stage=row["stage"],
        progress=float(row["progress"]),
        current_stage_progress=float(row["current_stage_progress"]),
        elapsed_ms=elapsed_ms,
        queue_position=queue_position,
        message=str(row["message"]),
        result=_load_json(row.get("result_json")),
        error=(
            JobError.model_validate(error)
            if (error := _load_json(row.get("error_json")))
            else None
        ),
        created_at=row["created_at"],
        started_at=started_at,
        finished_at=finished_at,
    )


def _live_progress_snapshot(
    base: JobSnapshot | None,
    *,
    job_id: str,
    stage: JobStage,
    progress: float,
    current_stage_progress: float,
    message: str,
) -> JobSnapshot:
    if base is not None:
        started_at = base.started_at
        elapsed_ms = base.elapsed_ms
        if started_at is not None:
            elapsed_ms = max(0, round((utc_now() - started_at).total_seconds() * 1000))
        return base.model_copy(
            update={
                "state": "RUNNING",
                "stage": stage,
                "progress": _clamp(progress),
                "current_stage_progress": _clamp(current_stage_progress),
                "elapsed_ms": elapsed_ms,
                "queue_position": None,
                "message": message,
            }
        )

    return JobSnapshot(
        id=job_id,
        type="clip_create",
        state="RUNNING",
        stage=stage,
        progress=_clamp(progress),
        current_stage_progress=_clamp(current_stage_progress),
        elapsed_ms=None,
        queue_position=None,
        message=message,
        created_at=utc_now(),
    )


def job_sse_payload(snapshot: JobSnapshot) -> str:
    data = snapshot.model_dump(mode="json")
    return f"event: snapshot\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _dump_json(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"), default=str)


def _load_json(payload: Any) -> dict[str, Any] | None:
    if not payload:
        return None
    if isinstance(payload, dict):
        return payload
    return json.loads(str(payload))


def _datetime_or_none(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))
