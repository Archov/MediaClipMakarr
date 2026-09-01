"""Restart and interrupted-finalization recovery."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mediaclipmakarr.clip_library import embedded_revision_matches
from mediaclipmakarr.clips import insert_clip_if_missing

from .finalization import (
    install_metadata_revision,
    recover_pending_installation,
    remove_superseded_clip,
)
from .models import BlockingRunner, JobError
from .repository import (
    _dump_json,
    _load_json,
    commit_metadata_edit,
    fail_job,
    finish_job_success_without_token,
    utc_now,
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


async def recover_finalizing_jobs(
    engine: AsyncEngine,
    run_blocking: BlockingRunner,
    *,
    preserve_workdirs: bool = False,
    clip_root: Path | None = None,
    work_root: Path | None = None,
) -> list[str]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT job_id, clip_id, operation_type, temp_path, source_path, target_path, "
                    "expected_revision, clip_json "
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

        paths_are_safe = await run_blocking(
            _pending_paths_are_safe,
            row,
            clip_root,
            work_root,
        )
        if not paths_are_safe:
            await fail_job(
                engine,
                job_id,
                None,
                code="PENDING_PATH_REJECTED",
                message="A pending file operation referenced a path outside managed roots.",
            )
            await _delete_pending_operation(engine, job_id)
            recovered.append(job_id)
            continue

        if row["operation_type"] == "metadata_edit":
            source_path = Path(str(row["source_path"]))
            target_path = Path(str(row["target_path"]))
            temp_path = Path(str(row["temp_path"]))
            target_is_new = await run_blocking(
                embedded_revision_matches,
                target_path,
                str(row["clip_id"]),
                int(clip["revision"]),
            )
            if not target_is_new:
                if not await run_blocking(temp_path.exists):
                    await fail_job(
                        engine,
                        job_id,
                        None,
                        code="FINALIZATION_RECOVERY_FAILED",
                        message=(
                            "The old clip is intact, but the prepared metadata revision was "
                            "unavailable after restart."
                        ),
                        retryable=True,
                    )
                    recovered.append(job_id)
                    await _delete_pending_operation(engine, job_id)
                    continue
                await run_blocking(
                    install_metadata_revision, temp_path, source_path, target_path
                )
            installed_stat = await run_blocking(target_path.stat)
            clip["file_size_bytes"] = installed_stat.st_size
            clip["file_modified_ns"] = installed_stat.st_mtime_ns
            await commit_metadata_edit(
                engine, clip, expected_revision=int(row["expected_revision"])
            )
            await run_blocking(remove_superseded_clip, source_path, target_path)
            await finish_job_success_without_token(engine, job_id, clip=clip)
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


def _pending_paths_are_safe(
    row: object, clip_root: Path | None, work_root: Path | None
) -> bool:
    if clip_root is None or work_root is None:
        return True
    mapping = dict(row)  # type: ignore[arg-type]
    target = Path(str(mapping["target_path"])).resolve(strict=False)
    temp = Path(str(mapping["temp_path"])).resolve(strict=False)
    resolved_clips = clip_root.resolve(strict=False)
    resolved_work = work_root.resolve(strict=False)
    if not target.is_relative_to(resolved_clips) or not temp.is_relative_to(resolved_work):
        return False
    source_value = mapping.get("source_path")
    if source_value:
        source = Path(str(source_value)).resolve(strict=False)
        if not source.is_relative_to(resolved_clips):
            return False
    return True


async def _delete_pending_operation(engine: AsyncEngine, job_id: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM pending_file_operations WHERE job_id = :job_id"),
            {"job_id": job_id},
        )
