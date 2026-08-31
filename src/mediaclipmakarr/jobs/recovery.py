"""Restart and interrupted-finalization recovery."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mediaclipmakarr.clips import insert_clip_if_missing

from .finalization import recover_pending_installation
from .models import BlockingRunner, JobError
from .repository import _dump_json, _load_json, fail_job, finish_job_success_without_token, utc_now


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


async def recover_finalizing_jobs(
    engine: AsyncEngine, run_blocking: BlockingRunner, *, preserve_workdirs: bool = False
) -> list[str]:
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
            recover_pending_installation,
            Path(str(row["temp_path"])),
            Path(str(row["target_path"])),
            preserve_workdirs,
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
