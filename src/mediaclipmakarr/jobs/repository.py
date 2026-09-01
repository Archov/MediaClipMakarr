"""Durable job persistence and guarded state transitions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mediaclipmakarr.clip_library import (
    ClipRevisionConflict,
    MetadataEditJobPlan,
    ThumbnailJobPlan,
)
from mediaclipmakarr.render_plan import ClipRenderPlan

from .models import ClaimedJob, JobError, JobSnapshot, JobStage, JobState, JobUpdateConflict


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def enqueue_clip_create_job(engine: AsyncEngine, plan: ClipRenderPlan) -> JobSnapshot:
    return await _enqueue_job(engine, "clip_create", plan, "Clip render is queued.")


async def enqueue_thumbnail_job(
    engine: AsyncEngine, plan: ThumbnailJobPlan
) -> JobSnapshot:
    existing = await _find_active_job(engine, "thumbnail_generate", plan.operation_hash)
    if existing is not None:
        return existing
    return await _enqueue_job(
        engine, "thumbnail_generate", plan, "Thumbnail generation is queued."
    )


async def enqueue_metadata_edit_job(
    engine: AsyncEngine, plan: MetadataEditJobPlan
) -> JobSnapshot:
    return await _enqueue_job(
        engine, "clip_metadata_edit", plan, "Clip metadata update is queued."
    )


async def _enqueue_job(
    engine: AsyncEngine,
    job_type: str,
    plan: ClipRenderPlan | ThumbnailJobPlan | MetadataEditJobPlan,
    message: str,
) -> JobSnapshot:
    created_at = utc_now()
    operation_hash = getattr(plan, "render_plan_hash", None) or plan.operation_hash
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs "
                "(id, type, state, stage, progress, current_stage_progress, message, attempt, "
                "render_plan_json, render_plan_hash, created_at) "
                "VALUES (:id, :type, 'QUEUED', 'queued', 0, 0, :message, 0, "
                ":render_plan_json, :render_plan_hash, :created_at)"
            ),
            {
                "id": plan.job_id,
                "type": job_type,
                "message": message,
                "render_plan_json": _dump_json(plan.model_dump(mode="json")),
                "render_plan_hash": operation_hash,
                "created_at": created_at,
            },
        )
    snapshot = await get_job_snapshot(engine, plan.job_id)
    if snapshot is None:
        raise RuntimeError("The queued job could not be read back from SQLite.")
    return snapshot


async def _find_active_job(
    engine: AsyncEngine, job_type: str, operation_hash: str
) -> JobSnapshot | None:
    async with engine.connect() as connection:
        job_id = await connection.scalar(
            text(
                "SELECT id FROM jobs WHERE type = :type AND render_plan_hash = :hash "
                "AND state IN ('QUEUED', 'RUNNING', 'FINALIZING') ORDER BY created_at LIMIT 1"
            ),
            {"type": job_type, "hash": operation_hash},
        )
    return await get_job_snapshot(engine, str(job_id)) if job_id else None


async def get_job_snapshot(engine: AsyncEngine, job_id: str) -> JobSnapshot | None:
    async with engine.connect() as connection:
        row = (
            (await connection.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}))
            .mappings()
            .first()
        )
        if row is None:
            return None
        queue_position = None
        if row["state"] == "QUEUED":
            queue_position = await connection.scalar(
                text(
                    "SELECT COUNT(*) FROM jobs WHERE state = 'QUEUED' AND created_at <= :created_at"
                ),
                {"created_at": row["created_at"]},
            )
    return _snapshot_from_row(dict(row), queue_position=int(queue_position or 0) or None)


async def claim_next_job(engine: AsyncEngine, run_token: str) -> ClaimedJob | None:
    started_at = utc_now()
    async with engine.begin() as connection:
        candidate = (
            (
                await connection.execute(
                    text(
                        "SELECT id, type, render_plan_json FROM jobs "
                        "WHERE state = 'QUEUED' ORDER BY created_at LIMIT 1"
                    )
                )
            )
            .mappings()
            .first()
        )
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

    job_type = str(candidate["type"])
    plan_class = {
        "clip_create": ClipRenderPlan,
        "thumbnail_generate": ThumbnailJobPlan,
        "clip_metadata_edit": MetadataEditJobPlan,
    }.get(job_type)
    if plan_class is None:
        raise ValueError(f"Unsupported queued job type: {job_type}")
    return ClaimedJob(
        id=str(candidate["id"]),
        run_token=run_token,
        type=job_type,
        render_plan=plan_class.model_validate_json(str(candidate["render_plan_json"])),
    )


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
    message: str = "Clip render completed.",
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
                "message": message,
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
    alternatives: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    finished_at = utc_now()
    error = JobError(
        code=code,
        message=message,
        retryable=retryable,
        alternatives=alternatives or [],
        context=context or {},
    )
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


async def finish_running_job_success(
    engine: AsyncEngine,
    job_id: str,
    run_token: str,
    *,
    result_payload: dict[str, Any],
    message: str,
) -> None:
    finished_at = utc_now()
    async with engine.begin() as connection:
        result = await connection.execute(
            text(
                "UPDATE jobs SET state = 'SUCCEEDED', stage = 'complete', progress = 1, "
                "current_stage_progress = 1, finished_at = :finished_at, run_token = NULL, "
                "result_json = :result_json, message = :message "
                "WHERE id = :id AND state = 'RUNNING' AND run_token = :run_token"
            ),
            {
                "id": job_id,
                "run_token": run_token,
                "finished_at": finished_at,
                "result_json": _dump_json(result_payload),
                "message": message,
            },
        )
        if result.rowcount != 1:
            raise JobUpdateConflict(f"Job {job_id} could not be completed.")


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


async def create_pending_metadata_operation(
    engine: AsyncEngine,
    *,
    job_id: str,
    clip_id: str,
    temp_path: Path,
    source_path: Path,
    destination: Path,
    expected_revision: int,
    operation_hash: str,
    clip: dict[str, Any],
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO pending_file_operations "
                "(id, job_id, clip_id, operation_type, temp_path, source_path, target_path, "
                "expected_revision, render_plan_hash, clip_json, created_at) "
                "VALUES (:id, :job_id, :clip_id, 'metadata_edit', :temp_path, :source_path, "
                ":target_path, :expected_revision, :render_plan_hash, :clip_json, :created_at)"
            ),
            {
                "id": f"pending-{uuid4()}",
                "job_id": job_id,
                "clip_id": clip_id,
                "temp_path": str(temp_path),
                "source_path": str(source_path),
                "target_path": str(destination),
                "expected_revision": expected_revision,
                "render_plan_hash": operation_hash,
                "clip_json": _dump_json(clip),
                "created_at": utc_now(),
            },
        )


async def commit_metadata_edit(
    engine: AsyncEngine,
    clip: dict[str, Any],
    *,
    expected_revision: int,
) -> None:
    async with engine.begin() as connection:
        current = (
            await connection.execute(
                text("SELECT * FROM clips WHERE id = :id"), {"id": clip["id"]}
            )
        ).mappings().first()
        if current is not None and int(current["revision"]) == int(clip["revision"]):
            return
        if current is None or int(current["revision"]) != expected_revision:
            raise ClipRevisionConflict("Clip revision changed before metadata finalization.")
        current_payload = dict(current)
        await connection.execute(
            text(
                "INSERT OR IGNORE INTO clip_revisions "
                "(clip_id, revision, metadata_json, file_path, created_at) "
                "VALUES (:clip_id, :revision, :metadata_json, :file_path, :created_at)"
            ),
            {
                "clip_id": current_payload["id"],
                "revision": current_payload["revision"],
                "metadata_json": _dump_json(current_payload),
                "file_path": current_payload["file_path"],
                "created_at": utc_now(),
            },
        )
        fields = (
            "title",
            "custom_title",
            "automatic_title",
            "library",
            "media_type",
            "movie_title",
            "movie_year",
            "show_name",
            "episode_title",
            "season_number",
            "episode_number",
            "clip_number",
            "file_path",
            "file_size_bytes",
            "file_modified_ns",
            "revision",
            "updated_at",
            "thumbnail_path",
            "thumbnail_source_size",
            "thumbnail_source_modified_ns",
        )
        assignments = ", ".join(f"{field} = :{field}" for field in fields)
        result = await connection.execute(
            text(
                f"UPDATE clips SET {assignments} WHERE id = :id AND revision = :expected_revision"
            ),
            {**clip, "expected_revision": expected_revision},
        )
        if result.rowcount != 1:
            raise ClipRevisionConflict("Clip revision changed before metadata finalization.")
        await connection.execute(
            text(
                "INSERT OR IGNORE INTO clip_revisions "
                "(clip_id, revision, metadata_json, file_path, created_at) "
                "VALUES (:clip_id, :revision, :metadata_json, :file_path, :created_at)"
            ),
            {
                "clip_id": clip["id"],
                "revision": clip["revision"],
                "metadata_json": _dump_json(clip),
                "file_path": clip["file_path"],
                "created_at": utc_now(),
            },
        )
        await connection.execute(
            text(
                "DELETE FROM clip_revisions WHERE clip_id = :clip_id AND id NOT IN "
                "(SELECT id FROM clip_revisions WHERE clip_id = :clip_id "
                "ORDER BY revision DESC LIMIT 25)"
            ),
            {"clip_id": clip["id"]},
        )


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
            JobError.model_validate(error) if (error := _load_json(row.get("error_json"))) else None
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
