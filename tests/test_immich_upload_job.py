from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import text

import mediaclipmakarr.jobs.repository as repository_module
import mediaclipmakarr.jobs.runner as runner_module
from mediaclipmakarr.clip_library import ClipRevisionConflict, build_immich_upload_plan
from mediaclipmakarr.clips import get_clip, insert_clip, parse_stored_immich_tag_ids
from mediaclipmakarr.config import Settings
from mediaclipmakarr.database import create_database_engine, upgrade_database
from mediaclipmakarr.immich import (
    ImmichAssetNotFoundError,
    ImmichInvalidResponseError,
)
from mediaclipmakarr.jobs import (
    ImmichJobSettings,
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


def _make_runner(
    engine,
    tmp_path,
    *,
    immich_url: str = IMMICH_URL,
    default_tag: str = "",
    tag_library: bool = False,
    tag_show: bool = False,
    tag_episode: bool = False,
) -> JobRunner:
    async def immich_settings_loader() -> ImmichJobSettings:
        return ImmichJobSettings(
            url=immich_url,
            api_key="test-key",
            default_tag=default_tag,
            tag_library=tag_library,
            tag_show=tag_show,
            tag_episode=tag_episode,
        )

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
    assert snapshot.result == {
        "clip_id": "clip-one",
        "immich_asset_id": "asset-remote-1",
        "description_set": True,
        "tags_applied": [],
    }
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
    assert first_snapshot.result == {
        "clip_id": "clip-one",
        "immich_asset_id": "asset-remote-1",
        "description_set": False,
        "tags_applied": [],
    }
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
    assert snapshot.result == {
        "clip_id": "clip-one",
        "immich_asset_id": "asset-remote-new",
        "description_set": False,
        "tags_applied": [],
    }
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


@pytest.mark.asyncio
async def test_immich_upload_rejects_a_fingerprint_mismatch_before_uploading(
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
        return "should-not-upload"

    monkeypatch.setattr(runner_module, "upload_immich_asset_sync", fake_upload)
    try:
        await insert_clip(engine, clip_payload(source))
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, plan)
        claimed = await _claim_immich_upload_job(engine, "run-one")

        # The managed file changes in place after the clip row was read but
        # before the (durable) upload actually runs.
        source.write_bytes(b"different bytes entirely")

        runner = _make_runner(engine, tmp_path)
        with pytest.raises(ClipRevisionConflict):
            await runner._execute_claimed_job(claimed)
    finally:
        await engine.dispose()

    assert upload_calls == []


@pytest.mark.asyncio
async def test_enqueue_immich_upload_job_returns_winner_on_index_conflict(tmp_path) -> None:
    database_path = tmp_path / "application.db"
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    try:
        # Pre-seed an active job for this clip, simulating a concurrent request
        # that already won, then force the app-level "already active" check to
        # miss on its first call so our own insert genuinely races the DB-level
        # partial unique index instead of being short-circuited by it.
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO jobs (id, type, state, stage, progress, "
                    "current_stage_progress, message, attempt, render_plan_json, "
                    "render_plan_hash, created_at) "
                    "VALUES ('job-winner', 'immich_upload', 'QUEUED', 'queued', 0, 0, "
                    "'Immich upload is queued.', 0, '{}', 'clip-one', :created_at)"
                ),
                {"created_at": datetime(2026, 1, 1)},
            )

        real_find_active_job = repository_module._find_active_job
        calls = {"count": 0}

        async def flaky_find_active_job(engine_, job_type, operation_hash):
            calls["count"] += 1
            if calls["count"] == 1:
                return None
            return await real_find_active_job(engine_, job_type, operation_hash)

        original = repository_module._find_active_job
        repository_module._find_active_job = flaky_find_active_job
        try:
            plan = build_immich_upload_plan({"id": "clip-one"})
            result = await enqueue_immich_upload_job(engine, plan)
        finally:
            repository_module._find_active_job = original

        row_count = await engine_scalar(
            engine,
            "SELECT COUNT(*) FROM jobs WHERE render_plan_hash = 'clip-one' "
            "AND type = 'immich_upload'",
        )
    finally:
        await engine.dispose()

    assert result.id == "job-winner"
    assert calls["count"] == 2
    # Our own insert must not have persisted alongside the pre-seeded winner.
    assert row_count == 1


async def engine_scalar(engine, sql: str):
    async with engine.connect() as connection:
        return await connection.scalar(text(sql))


@pytest.mark.asyncio
async def test_immich_upload_applies_default_and_hierarchy_tags_on_success(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / "Pilot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"clip bytes")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    def fake_upload(source_path, url, api_key, *, file_created_at, file_modified_at):
        return "asset-remote-1"

    async def fake_set_description(asset_id, description, url, api_key):
        return None

    upsert_calls: list[list[str]] = []

    async def fake_upsert_tags(tag_paths, url, api_key):
        upsert_calls.append(tag_paths)
        return {path: f"tag-id-{index}" for index, path in enumerate(tag_paths)}

    tag_asset_calls: list[tuple[str, list[str]]] = []

    async def fake_tag_assets(asset_id, tag_ids, url, api_key):
        tag_asset_calls.append((asset_id, tag_ids))

    monkeypatch.setattr(runner_module, "upload_immich_asset_sync", fake_upload)
    monkeypatch.setattr(runner_module, "set_immich_asset_description", fake_set_description)
    monkeypatch.setattr(runner_module, "upsert_immich_tags", fake_upsert_tags)
    monkeypatch.setattr(runner_module, "tag_immich_assets", fake_tag_assets)
    try:
        await insert_clip(engine, clip_payload(source))
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, plan)
        claimed = await _claim_immich_upload_job(engine, "run-one")

        runner = _make_runner(
            engine,
            tmp_path,
            default_tag="plex",
            tag_library=True,
            tag_show=False,
            tag_episode=False,
        )
        await runner._execute_claimed_job(claimed)
        snapshot = await get_job_snapshot(engine, claimed.id)
    finally:
        await engine.dispose()

    assert snapshot is not None
    assert snapshot.state == "SUCCEEDED"
    assert snapshot.result == {
        "clip_id": "clip-one",
        "immich_asset_id": "asset-remote-1",
        "description_set": True,
        "tags_applied": ["plex", "TV Shows"],
    }
    assert upsert_calls == [["plex", "TV Shows"]]
    assert tag_asset_calls == [("asset-remote-1", ["tag-id-0", "tag-id-1"])]


@pytest.mark.asyncio
async def test_immich_upload_partial_when_tagging_fails_but_description_succeeds(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / "Pilot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"clip bytes")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    def fake_upload(source_path, url, api_key, *, file_created_at, file_modified_at):
        return "asset-remote-1"

    async def fake_set_description(asset_id, description, url, api_key):
        return None

    async def fake_upsert_tags(tag_paths, url, api_key):
        raise ImmichInvalidResponseError("tag service unavailable")

    monkeypatch.setattr(runner_module, "upload_immich_asset_sync", fake_upload)
    monkeypatch.setattr(runner_module, "set_immich_asset_description", fake_set_description)
    monkeypatch.setattr(runner_module, "upsert_immich_tags", fake_upsert_tags)
    try:
        await insert_clip(engine, clip_payload(source))
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, plan)
        claimed = await _claim_immich_upload_job(engine, "run-one")

        runner = _make_runner(engine, tmp_path, default_tag="plex")
        await runner._execute_claimed_job(claimed)
        snapshot = await get_job_snapshot(engine, claimed.id)
    finally:
        await engine.dispose()

    assert snapshot is not None
    assert snapshot.state == "PARTIAL"
    assert snapshot.result == {
        "clip_id": "clip-one",
        "immich_asset_id": "asset-remote-1",
        "description_set": True,
        "tags_applied": [],
    }
    assert snapshot.error is not None
    assert snapshot.error.code == "IMMICH_INVALID_RESPONSE"


@pytest.mark.asyncio
async def test_immich_upload_partial_when_description_fails_but_tagging_succeeds(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / "Pilot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"clip bytes")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    def fake_upload(source_path, url, api_key, *, file_created_at, file_modified_at):
        return "asset-remote-1"

    async def fake_set_description(asset_id, description, url, api_key):
        raise ImmichInvalidResponseError("description service unavailable")

    async def fake_upsert_tags(tag_paths, url, api_key):
        return {path: f"tag-id-{index}" for index, path in enumerate(tag_paths)}

    async def fake_tag_assets(asset_id, tag_ids, url, api_key):
        return None

    monkeypatch.setattr(runner_module, "upload_immich_asset_sync", fake_upload)
    monkeypatch.setattr(runner_module, "set_immich_asset_description", fake_set_description)
    monkeypatch.setattr(runner_module, "upsert_immich_tags", fake_upsert_tags)
    monkeypatch.setattr(runner_module, "tag_immich_assets", fake_tag_assets)
    try:
        await insert_clip(engine, clip_payload(source))
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, plan)
        claimed = await _claim_immich_upload_job(engine, "run-one")

        runner = _make_runner(engine, tmp_path, default_tag="plex")
        await runner._execute_claimed_job(claimed)
        snapshot = await get_job_snapshot(engine, claimed.id)
    finally:
        await engine.dispose()

    assert snapshot is not None
    assert snapshot.state == "PARTIAL"
    assert snapshot.result == {
        "clip_id": "clip-one",
        "immich_asset_id": "asset-remote-1",
        "description_set": False,
        "tags_applied": ["plex"],
    }
    assert snapshot.error is not None
    assert snapshot.error.code == "IMMICH_INVALID_RESPONSE"


@pytest.mark.asyncio
async def test_immich_upload_combines_errors_when_description_and_tagging_both_fail(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / "Pilot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"clip bytes")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    def fake_upload(source_path, url, api_key, *, file_created_at, file_modified_at):
        return "asset-remote-1"

    async def fake_set_description(asset_id, description, url, api_key):
        raise ImmichInvalidResponseError("description service unavailable")

    async def fake_upsert_tags(tag_paths, url, api_key):
        raise ImmichInvalidResponseError("tag service unavailable")

    monkeypatch.setattr(runner_module, "upload_immich_asset_sync", fake_upload)
    monkeypatch.setattr(runner_module, "set_immich_asset_description", fake_set_description)
    monkeypatch.setattr(runner_module, "upsert_immich_tags", fake_upsert_tags)
    try:
        await insert_clip(engine, clip_payload(source))
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, plan)
        claimed = await _claim_immich_upload_job(engine, "run-one")

        runner = _make_runner(engine, tmp_path, default_tag="plex")
        await runner._execute_claimed_job(claimed)
        snapshot = await get_job_snapshot(engine, claimed.id)
    finally:
        await engine.dispose()

    assert snapshot is not None
    assert snapshot.state == "PARTIAL"
    assert snapshot.result == {
        "clip_id": "clip-one",
        "immich_asset_id": "asset-remote-1",
        "description_set": False,
        "tags_applied": [],
    }
    assert snapshot.error is not None
    assert snapshot.error.code == "IMMICH_ORGANIZE_FAILED"
    assert "description service unavailable" in snapshot.error.message
    assert "tag service unavailable" in snapshot.error.message


@pytest.mark.asyncio
async def test_immich_upload_skips_tagging_step_when_nothing_configured(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / "Pilot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"clip bytes")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    def fake_upload(source_path, url, api_key, *, file_created_at, file_modified_at):
        return "asset-remote-1"

    async def fake_set_description(asset_id, description, url, api_key):
        return None

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("upsert_immich_tags should not be called with nothing configured")

    monkeypatch.setattr(runner_module, "upload_immich_asset_sync", fake_upload)
    monkeypatch.setattr(runner_module, "set_immich_asset_description", fake_set_description)
    monkeypatch.setattr(runner_module, "upsert_immich_tags", fail_if_called)
    try:
        await insert_clip(engine, clip_payload(source))
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, plan)
        claimed = await _claim_immich_upload_job(engine, "run-one")

        # Default runner from _make_runner has all tag settings off/empty.
        runner = _make_runner(engine, tmp_path)
        await runner._execute_claimed_job(claimed)
        snapshot = await get_job_snapshot(engine, claimed.id)
    finally:
        await engine.dispose()

    assert snapshot is not None
    assert snapshot.state == "SUCCEEDED"
    assert snapshot.result["tags_applied"] == []


@pytest.mark.asyncio
async def test_immich_upload_falls_back_to_embedded_identity_when_no_fingerprint_baseline(
    tmp_path, monkeypatch
) -> None:
    """Clips recorded before file_size_bytes/file_modified_ns existed (or inserted
    through a path that never set them) have no byte-level baseline to compare
    against — the fingerprint check must fall back to the clip id/revision envelope
    every rendered file carries in its `comment` metadata tag, and proceed when it
    matches."""
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / "Pilot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b'clip bytes MediaClipMakarr {"clipId":"clip-one","revision":1}')
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    def fake_upload(source_path, url, api_key, *, file_created_at, file_modified_at):
        return "asset-remote-1"

    async def fake_set_description(asset_id, description, url, api_key):
        return None

    monkeypatch.setattr(runner_module, "upload_immich_asset_sync", fake_upload)
    monkeypatch.setattr(runner_module, "set_immich_asset_description", fake_set_description)
    try:
        payload = clip_payload(source)
        payload["file_size_bytes"] = None
        payload["file_modified_ns"] = None
        await insert_clip(engine, payload)
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        assert row["file_size_bytes"] is None
        plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, plan)
        claimed = await _claim_immich_upload_job(engine, "run-one")

        runner = _make_runner(engine, tmp_path)
        await runner._execute_claimed_job(claimed)
        snapshot = await get_job_snapshot(engine, claimed.id)
    finally:
        await engine.dispose()

    assert snapshot is not None
    assert snapshot.state == "SUCCEEDED"


@pytest.mark.asyncio
async def test_immich_upload_rejects_unknown_identity_when_no_fingerprint_baseline(
    tmp_path, monkeypatch
) -> None:
    """Without a byte-level baseline or a matching embedded identity envelope, the
    file at file_path is an unknown quantity — reject it rather than uploading
    whatever currently occupies that path under the clip's name."""
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / "Pilot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"unrelated bytes with no identity envelope")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    upload_calls: list[str] = []

    def fake_upload(source_path, url, api_key, *, file_created_at, file_modified_at):
        upload_calls.append(str(source_path))
        return "asset-remote-1"

    monkeypatch.setattr(runner_module, "upload_immich_asset_sync", fake_upload)
    try:
        payload = clip_payload(source)
        payload["file_size_bytes"] = None
        payload["file_modified_ns"] = None
        await insert_clip(engine, payload)
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, plan)
        claimed = await _claim_immich_upload_job(engine, "run-one")

        runner = _make_runner(engine, tmp_path)
        with pytest.raises(ClipRevisionConflict):
            await runner._execute_claimed_job(claimed)
    finally:
        await engine.dispose()

    assert upload_calls == []


@pytest.mark.asyncio
async def test_immich_upload_removes_stale_tags_no_longer_resolved(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / "Pilot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"clip bytes")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    def fake_upload(source_path, url, api_key, *, file_created_at, file_modified_at):
        return "asset-remote-1"

    async def fake_set_description(asset_id, description, url, api_key):
        return None

    async def fake_upsert_tags(tag_paths, url, api_key):
        return {path: f"tag-id-for-{path}" for path in tag_paths}

    tag_asset_calls: list[tuple[str, list[str]]] = []

    async def fake_tag_assets(asset_id, tag_ids, url, api_key):
        tag_asset_calls.append((asset_id, tag_ids))

    untag_calls: list[tuple[str, list[str]]] = []

    async def fake_untag_assets(tag_id, asset_ids, url, api_key):
        untag_calls.append((tag_id, asset_ids))

    monkeypatch.setattr(runner_module, "upload_immich_asset_sync", fake_upload)
    monkeypatch.setattr(runner_module, "set_immich_asset_description", fake_set_description)
    monkeypatch.setattr(runner_module, "upsert_immich_tags", fake_upsert_tags)
    monkeypatch.setattr(runner_module, "tag_immich_assets", fake_tag_assets)
    monkeypatch.setattr(runner_module, "untag_immich_assets", fake_untag_assets)
    try:
        await insert_clip(engine, clip_payload(source))
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, plan)
        claimed = await _claim_immich_upload_job(engine, "run-one")

        runner = _make_runner(engine, tmp_path, tag_library=True)
        await runner._execute_claimed_job(claimed)
        clip_after_first = await get_clip(engine, "clip-one", clip_root)

        # Simulate the library having been renamed since the first upload (what a
        # metadata edit would produce), without needing to run that job here.
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE clips SET library = :library WHERE id = :id"),
                {"library": "Anime", "id": "clip-one"},
            )
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        retry_plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, retry_plan)
        retry_claimed = await _claim_immich_upload_job(engine, "run-two")
        await runner._execute_claimed_job(retry_claimed)
        clip_after_second = await get_clip(engine, "clip-one", clip_root)
        second_snapshot = await get_job_snapshot(engine, retry_claimed.id)
    finally:
        await engine.dispose()

    assert clip_after_first is not None
    assert json.loads(clip_after_first["immich_tag_ids"]) == ["tag-id-for-TV Shows"]

    # The stale "TV Shows" tag must have been removed, and only the new "Anime"
    # tag added — never both accumulating.
    assert untag_calls == [("tag-id-for-TV Shows", ["asset-remote-1"])]
    assert tag_asset_calls[-1] == ("asset-remote-1", ["tag-id-for-Anime"])
    assert clip_after_second is not None
    assert json.loads(clip_after_second["immich_tag_ids"]) == ["tag-id-for-Anime"]
    assert second_snapshot is not None
    assert second_snapshot.state == "SUCCEEDED"


@pytest.mark.asyncio
async def test_immich_upload_does_not_carry_stale_tag_ids_across_a_server_change(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / "Pilot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"clip bytes")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    def fake_upload(source_path, url, api_key, *, file_created_at, file_modified_at):
        return f"asset-for-{url}"

    async def fake_set_description(asset_id, description, url, api_key):
        return None

    async def fake_upsert_tags(tag_paths, url, api_key):
        return {path: f"tag-id-for-{url}" for path in tag_paths}

    async def fake_tag_assets(asset_id, tag_ids, url, api_key):
        return None

    untag_calls: list[tuple[str, list[str]]] = []

    async def fake_untag_assets(tag_id, asset_ids, url, api_key):
        untag_calls.append((tag_id, asset_ids))

    monkeypatch.setattr(runner_module, "upload_immich_asset_sync", fake_upload)
    monkeypatch.setattr(runner_module, "set_immich_asset_description", fake_set_description)
    monkeypatch.setattr(runner_module, "upsert_immich_tags", fake_upsert_tags)
    monkeypatch.setattr(runner_module, "tag_immich_assets", fake_tag_assets)
    monkeypatch.setattr(runner_module, "untag_immich_assets", fake_untag_assets)
    try:
        await insert_clip(engine, clip_payload(source))
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, plan)
        claimed = await _claim_immich_upload_job(engine, "run-one")

        runner_a = _make_runner(engine, tmp_path, immich_url=IMMICH_URL, tag_library=True)
        await runner_a._execute_claimed_job(claimed)
        clip_after_first = await get_clip(engine, "clip-one", clip_root)

        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        second_plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, second_plan)
        second_claimed = await _claim_immich_upload_job(engine, "run-two")
        runner_b = _make_runner(
            engine, tmp_path, immich_url=OTHER_IMMICH_URL, tag_library=True
        )
        await runner_b._execute_claimed_job(second_claimed)
        clip_after_second = await get_clip(engine, "clip-one", clip_root)
    finally:
        await engine.dispose()

    assert clip_after_first is not None
    assert json.loads(clip_after_first["immich_tag_ids"]) == [f"tag-id-for-{IMMICH_URL}"]

    # The fresh upload to the second server must never have tried to remove a
    # tag id that only ever existed on the first server.
    assert untag_calls == []
    assert clip_after_second is not None
    assert json.loads(clip_after_second["immich_tag_ids"]) == [f"tag-id-for-{OTHER_IMMICH_URL}"]


@pytest.mark.asyncio
async def test_immich_upload_clears_stale_tag_cache_on_server_change_with_no_tags_configured(
    tmp_path, monkeypatch
) -> None:
    """When the configured server changes and no tags are configured on the retry,
    the tag-diffing block never runs at all (nothing to add, and the stale cache is
    deliberately not trusted as "previous"). The old server's tag ids must still be
    cleared from the durable cache — otherwise a later run that reuses this new
    association would load them back and send them to the new server."""
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / "Pilot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"clip bytes")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    def fake_upload(source_path, url, api_key, *, file_created_at, file_modified_at):
        return f"asset-for-{url}"

    async def fake_set_description(asset_id, description, url, api_key):
        return None

    async def fake_upsert_tags(tag_paths, url, api_key):
        return {path: f"tag-id-for-{url}" for path in tag_paths}

    async def fake_tag_assets(asset_id, tag_ids, url, api_key):
        return None

    monkeypatch.setattr(runner_module, "upload_immich_asset_sync", fake_upload)
    monkeypatch.setattr(runner_module, "set_immich_asset_description", fake_set_description)
    monkeypatch.setattr(runner_module, "upsert_immich_tags", fake_upsert_tags)
    monkeypatch.setattr(runner_module, "tag_immich_assets", fake_tag_assets)
    try:
        await insert_clip(engine, clip_payload(source))
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, plan)
        claimed = await _claim_immich_upload_job(engine, "run-one")

        runner_a = _make_runner(engine, tmp_path, immich_url=IMMICH_URL, tag_library=True)
        await runner_a._execute_claimed_job(claimed)
        clip_after_first = await get_clip(engine, "clip-one", clip_root)

        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        second_plan = build_immich_upload_plan(row)
        await enqueue_immich_upload_job(engine, second_plan)
        second_claimed = await _claim_immich_upload_job(engine, "run-two")
        # No tags configured this time — the tag-diffing block is skipped entirely.
        runner_b = _make_runner(engine, tmp_path, immich_url=OTHER_IMMICH_URL)
        await runner_b._execute_claimed_job(second_claimed)
        clip_after_second = await get_clip(engine, "clip-one", clip_root)
    finally:
        await engine.dispose()

    assert clip_after_first is not None
    assert json.loads(clip_after_first["immich_tag_ids"]) == [f"tag-id-for-{IMMICH_URL}"]

    assert clip_after_second is not None
    assert clip_after_second["immich_server_url"] == OTHER_IMMICH_URL
    assert not parse_stored_immich_tag_ids(clip_after_second["immich_tag_ids"])
