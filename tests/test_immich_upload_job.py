from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import text

import mediaclipmakarr.jobs.runner as runner_module
from mediaclipmakarr.clip_library import build_immich_upload_plan
from mediaclipmakarr.clips import get_clip, insert_clip
from mediaclipmakarr.config import Settings
from mediaclipmakarr.database import create_database_engine, upgrade_database
from mediaclipmakarr.immich import ImmichAssetNotFoundError, ImmichInvalidResponseError
from mediaclipmakarr.jobs import (
    JobEventBroker,
    JobRunner,
    claim_next_job,
    enqueue_immich_upload_job,
    get_job_snapshot,
)

IMMICH_URL = "http://immich.example:2283"
OTHER_IMMICH_URL = "http://other-immich.example:2283"


async def run_blocking(function, *args, **kwargs):
    return await asyncio.to_thread(function, *args, **kwargs)


def clip_payload(path: Path, *, clip_id: str = "clip-one", title: str = "Pilot"):
    stat = path.stat()
    now = datetime(2026, 8, 31, 12, 0)
    return {
        "id": clip_id,
        "title": title,
        "automatic_title": title,
        "library": "TV Shows",
        "media_type": "episode",
        "file_path": str(path),
        "duration_ms": 10_000,
        "revision": 1,
        "source_start_ms": 1_000,
        "source_end_ms": 11_000,
        "source_path": "D:/read-only/source.mkv",
        "source_size_bytes": 999,
        "source_modified_at": now,
        "selected_audio_stream_index": 1,
        "render_plan_hash": "a" * 64,
        "created_at": now,
        "updated_at": now,
        "show_name": "Example Show",
        "episode_title": "Pilot",
        "season_number": 1,
        "episode_number": 1,
        "file_size_bytes": stat.st_size,
        "file_modified_ns": stat.st_mtime_ns,
    }


def _make_runner(engine, tmp_path, *, immich_url: str = IMMICH_URL) -> JobRunner:
    async def immich_settings_loader() -> tuple[str | None, str | None]:
        return immich_url, "test-key"

    return JobRunner(
        engine,
        Settings(
            _env_file=None,
            work_dir=tmp_path / "work",
            clip_dir=tmp_path / "clips",
            thumbnail_dir=tmp_path / "thumbnails",
        ),
        run_blocking=run_blocking,
        events=JobEventBroker(),
        immich_settings_loader=immich_settings_loader,
    )


async def _claim_immich_upload_job(engine, run_token: str):
    claimed = await claim_next_job(engine, run_token)
    assert claimed is not None
    assert claimed.type == "immich_upload"
    return claimed


@pytest.mark.asyncio
async def test_immich_upload_succeeds_end_to_end(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / "Pilot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"clip bytes")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    upload_calls: list[str] = []
    description_calls: list[str] = []

    def fake_upload(source_path, url, api_key, *, file_created_at, file_modified_at):
        upload_calls.append(url)
        return "asset-remote-1"

    async def fake_set_description(asset_id, description, url, api_key):
        description_calls.append(asset_id)

    monkeypatch.setattr(runner_module, "upload_immich_asset_sync", fake_upload)
    monkeypatch.setattr(runner_module, "set_immich_asset_description", fake_set_description)
    try:
        await insert_clip(engine, clip_payload(source))
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, plan)
        claimed = await _claim_immich_upload_job(engine, "run-one")

        runner = _make_runner(engine, tmp_path)
        await runner._execute_claimed_job(claimed)

        snapshot = await get_job_snapshot(engine, claimed.id)
        clip = await get_clip(engine, "clip-one", clip_root)
    finally:
        await engine.dispose()

    assert upload_calls == [IMMICH_URL]
    assert description_calls == ["asset-remote-1"]
    assert snapshot is not None
    assert snapshot.state == "SUCCEEDED"
    assert snapshot.result == {"clip_id": "clip-one", "immich_asset_id": "asset-remote-1"}
    assert clip is not None
    assert clip["immich_asset_id"] == "asset-remote-1"
    assert clip["immich_server_url"] == IMMICH_URL


@pytest.mark.asyncio
async def test_immich_upload_partial_on_description_failure_then_retry_succeeds(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / "Pilot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"clip bytes")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    upload_calls: list[str] = []
    description_attempts = {"count": 0}

    def fake_upload(source_path, url, api_key, *, file_created_at, file_modified_at):
        upload_calls.append(url)
        return "asset-remote-1"

    async def fake_set_description(asset_id, description, url, api_key):
        description_attempts["count"] += 1
        if description_attempts["count"] == 1:
            raise ImmichInvalidResponseError("temporary failure")

    monkeypatch.setattr(runner_module, "upload_immich_asset_sync", fake_upload)
    monkeypatch.setattr(runner_module, "set_immich_asset_description", fake_set_description)
    try:
        await insert_clip(engine, clip_payload(source))
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        plan = build_immich_upload_plan(row)

        await enqueue_immich_upload_job(engine, plan)
        claimed = await _claim_immich_upload_job(engine, "run-one")
        runner = _make_runner(engine, tmp_path)
        await runner._execute_claimed_job(claimed)
        first_snapshot = await get_job_snapshot(engine, claimed.id)

        # Retry: a fresh plan/job for the same clip.
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        retry_plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, retry_plan)
        retry_claimed = await _claim_immich_upload_job(engine, "run-two")
        await runner._execute_claimed_job(retry_claimed)
        second_snapshot = await get_job_snapshot(engine, retry_claimed.id)
    finally:
        await engine.dispose()

    assert first_snapshot is not None
    assert first_snapshot.state == "PARTIAL"
    assert first_snapshot.result == {"clip_id": "clip-one", "immich_asset_id": "asset-remote-1"}
    assert first_snapshot.error is not None
    assert first_snapshot.error.code == "IMMICH_INVALID_RESPONSE"

    assert second_snapshot is not None
    assert second_snapshot.state == "SUCCEEDED"
    # The retry must not have re-uploaded — the asset id/server url were already stored.
    assert upload_calls == [IMMICH_URL]
    assert description_attempts["count"] == 2


@pytest.mark.asyncio
async def test_immich_upload_reuploads_when_configured_server_changes(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / "Pilot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"clip bytes")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    upload_calls: list[str] = []

    def fake_upload(source_path, url, api_key, *, file_created_at, file_modified_at):
        upload_calls.append(url)
        return f"asset-for-{url}"

    async def fake_set_description(asset_id, description, url, api_key):
        return None

    monkeypatch.setattr(runner_module, "upload_immich_asset_sync", fake_upload)
    monkeypatch.setattr(runner_module, "set_immich_asset_description", fake_set_description)
    try:
        await insert_clip(engine, clip_payload(source))
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, plan)
        claimed = await _claim_immich_upload_job(engine, "run-one")
        runner_a = _make_runner(engine, tmp_path, immich_url=IMMICH_URL)
        await runner_a._execute_claimed_job(claimed)

        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        second_plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, second_plan)
        second_claimed = await _claim_immich_upload_job(engine, "run-two")
        runner_b = _make_runner(engine, tmp_path, immich_url=OTHER_IMMICH_URL)
        await runner_b._execute_claimed_job(second_claimed)

        clip = await get_clip(engine, "clip-one", clip_root)
    finally:
        await engine.dispose()

    assert upload_calls == [IMMICH_URL, OTHER_IMMICH_URL]
    assert clip is not None
    assert clip["immich_asset_id"] == f"asset-for-{OTHER_IMMICH_URL}"
    assert clip["immich_server_url"] == OTHER_IMMICH_URL


@pytest.mark.asyncio
async def test_immich_upload_partial_when_local_association_write_loses_a_race(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / "Pilot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"clip bytes")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    async def _inject_conflicting_association() -> None:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE clips SET immich_asset_id = :asset_id, "
                    "immich_server_url = :server_url WHERE id = :id"
                ),
                {"asset_id": "asset-from-elsewhere", "server_url": IMMICH_URL, "id": "clip-one"},
            )

    def fake_upload(source_path, url, api_key, *, file_created_at, file_modified_at):
        # Simulate a second writer landing between this job's `get_clip` read and its
        # own `set_clip_immich_asset_id` call, by writing a conflicting association
        # as a side effect of "the network call" completing.
        asyncio.run(_inject_conflicting_association())
        return "asset-remote-new"

    description_calls: list[str] = []

    async def fake_set_description(asset_id, description, url, api_key):
        description_calls.append(asset_id)

    monkeypatch.setattr(runner_module, "upload_immich_asset_sync", fake_upload)
    monkeypatch.setattr(runner_module, "set_immich_asset_description", fake_set_description)
    try:
        await insert_clip(engine, clip_payload(source))
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, plan)
        claimed = await _claim_immich_upload_job(engine, "run-one")
        runner = _make_runner(engine, tmp_path)
        await runner._execute_claimed_job(claimed)

        snapshot = await get_job_snapshot(engine, claimed.id)
        clip = await get_clip(engine, "clip-one", clip_root)
    finally:
        await engine.dispose()

    assert snapshot is not None
    assert snapshot.state == "PARTIAL"
    assert snapshot.result == {"clip_id": "clip-one", "immich_asset_id": "asset-remote-new"}
    assert snapshot.error is not None
    assert snapshot.error.code == "IMMICH_ASSET_ASSOCIATION_FAILED"
    # The description step must never have been reached for the orphaned remote asset.
    assert description_calls == []
    # The conflicting writer's association is what's actually stored, untouched.
    assert clip is not None
    assert clip["immich_asset_id"] == "asset-from-elsewhere"


@pytest.mark.asyncio
async def test_immich_upload_fails_without_clearing_stale_asset_on_reuse_path(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / "Pilot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"clip bytes")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    upload_calls: list[str] = []

    def fake_upload(source_path, url, api_key, *, file_created_at, file_modified_at):
        upload_calls.append(url)
        return "should-not-be-called"

    async def fake_set_description(asset_id, description, url, api_key):
        raise ImmichAssetNotFoundError(f"Immich asset {asset_id} no longer exists.")

    monkeypatch.setattr(runner_module, "upload_immich_asset_sync", fake_upload)
    monkeypatch.setattr(runner_module, "set_immich_asset_description", fake_set_description)
    try:
        await insert_clip(engine, clip_payload(source))
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE clips SET immich_asset_id = :asset_id, "
                    "immich_server_url = :server_url WHERE id = :id"
                ),
                {"asset_id": "already-stored-asset", "server_url": IMMICH_URL, "id": "clip-one"},
            )
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, plan)
        claimed = await _claim_immich_upload_job(engine, "run-one")
        runner = _make_runner(engine, tmp_path)
        await runner._execute_claimed_job(claimed)

        snapshot = await get_job_snapshot(engine, claimed.id)
        clip = await get_clip(engine, "clip-one", clip_root)
    finally:
        await engine.dispose()

    assert upload_calls == []  # reuse path — never re-uploaded
    assert snapshot is not None
    assert snapshot.state == "FAILED"
    assert snapshot.error is not None
    assert snapshot.error.code == "IMMICH_ASSET_NOT_FOUND"
    assert clip is not None
    assert clip["immich_asset_id"] == "already-stored-asset"
