from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import mediaclipmakarr.jobs.runner as runner_module
import mediaclipmakarr.main as main_module
from mediaclipmakarr.clip_library import (
    ClipDeleteSafetyError,
    ClipMetadataUpdate,
    ClipRevisionConflict,
    build_metadata_edit_plan,
    delete_clip,
    embedded_revision_matches,
    list_clips,
    list_filter_options,
    list_unlinked_clip_ids,
)
from mediaclipmakarr.clips import get_clip, insert_clip, set_clip_immich_asset_id
from mediaclipmakarr.config import Settings
from mediaclipmakarr.database import create_database_engine, upgrade_database
from mediaclipmakarr.health import MediaToolInspection
from mediaclipmakarr.jobs import (
    JobEventBroker,
    JobRunner,
    claim_next_job,
    enqueue_metadata_edit_job,
    fail_job,
    get_job_snapshot,
)
from mediaclipmakarr.jobs.recovery import recover_finalizing_jobs
from mediaclipmakarr.jobs.repository import (
    create_pending_metadata_operation,
    transition_to_finalizing,
)


async def run_blocking(function, *args, **kwargs):
    return await asyncio.to_thread(function, *args, **kwargs)


def _envelope(**overrides: object) -> bytes:
    payload = {
        "application": "MediaClipMakarr",
        "schemaVersion": 4,
        "clipId": "clip-one",
        "revision": 1,
    }
    payload.update(overrides)
    return b"padding bytes MediaClipMakarr " + json.dumps(payload).encode()


def test_embedded_revision_matches_accepts_a_complete_envelope(tmp_path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(_envelope())

    assert embedded_revision_matches(path, "clip-one", 1) is True


def test_embedded_revision_matches_rejects_a_fabricated_two_field_marker(tmp_path) -> None:
    """A marker followed by only clipId/revision (no application/schemaVersion) must
    not be mistaken for a genuine recovery envelope — see PR discussion on #46/#55."""
    path = tmp_path / "clip.mp4"
    path.write_bytes(b'padding MediaClipMakarr {"clipId":"clip-one","revision":1}')

    assert embedded_revision_matches(path, "clip-one", 1) is False


def test_embedded_revision_matches_rejects_wrong_application(tmp_path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(_envelope(application="SomeOtherApp"))

    assert embedded_revision_matches(path, "clip-one", 1) is False


def test_embedded_revision_matches_rejects_missing_schema_version(tmp_path) -> None:
    path = tmp_path / "clip.mp4"
    payload = {
        "application": "MediaClipMakarr",
        "clipId": "clip-one",
        "revision": 1,
    }
    path.write_bytes(b"padding bytes MediaClipMakarr " + json.dumps(payload).encode())

    assert embedded_revision_matches(path, "clip-one", 1) is False


def test_embedded_revision_matches_rejects_mismatched_clip_id_or_revision(tmp_path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(_envelope())

    assert embedded_revision_matches(path, "clip-two", 1) is False
    assert embedded_revision_matches(path, "clip-one", 2) is False


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


@pytest.mark.asyncio
async def test_library_queries_search_filter_sort_and_paginate(tmp_path) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    first = clip_root / "TV Shows" / "Pilot.mp4"
    second = clip_root / "Movies" / "Film.mp4"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"episode")
    second.write_bytes(b"movie")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    try:
        episode_clip = clip_payload(first)
        episode_clip["library"] = "tv shows"
        await insert_clip(engine, episode_clip)
        movie = clip_payload(second, clip_id="clip-two", title="A Film")
        movie.update(
            {
                "library": "movies",
                "media_type": "movie",
                "movie_title": "A Film",
                "movie_year": 2026,
            }
        )
        await insert_clip(engine, movie)

        selected_media = await list_clips(
            engine,
            media=["Example Show", "A Film"],
        )
        options = await list_filter_options(engine, ["TV Shows", "Movies"])

        page = await list_clips(
            engine,
            search="film",
            library="Movies",
            media_type="movie",
            sort="title_asc",
            page=1,
            page_size=1,
        )
        all_clips = await list_clips(engine, page=2, page_size=None)
    finally:
        await engine.dispose()

    assert page.total == 1
    assert page.items[0].id == "clip-two"
    assert len(all_clips.items) == 2
    assert all_clips.page == 1
    assert all_clips.page_size == 2
    assert all_clips.pages == 1
    assert page.items[0].play_url == "/api/clips/clip-two/media"
    assert selected_media.total == 2
    assert options.libraries == ["Movies", "TV Shows"]
    assert options.movies == ["A Film"]
    assert options.shows == ["Example Show"]
    assert options.episodes[0].show_name == "Example Show"
    assert options.episodes[0].title == "Pilot"
    assert options.episodes[0].season_number == 1
    assert options.episodes[0].episode_number == 1


@pytest.mark.asyncio
async def test_list_unlinked_clip_ids_includes_stale_server_but_excludes_current_server(
    tmp_path,
) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    unlinked = clip_root / "TV Shows" / "Unlinked.mp4"
    stale = clip_root / "TV Shows" / "Stale.mp4"
    current = clip_root / "TV Shows" / "Current.mp4"
    unlinked.parent.mkdir(parents=True)
    unlinked.write_bytes(b"a")
    stale.write_bytes(b"b")
    current.write_bytes(b"c")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    try:
        await insert_clip(engine, clip_payload(unlinked, clip_id="clip-unlinked"))
        await insert_clip(
            engine, clip_payload(stale, clip_id="clip-stale", title="Stale")
        )
        await insert_clip(
            engine, clip_payload(current, clip_id="clip-current", title="Current")
        )
        await set_clip_immich_asset_id(
            engine, "clip-stale", "asset-old", "http://old-immich.example:2283"
        )
        await set_clip_immich_asset_id(
            engine, "clip-current", "asset-new", "http://immich.example:2283"
        )

        unlinked_ids = await list_unlinked_clip_ids(
            engine, "http://immich.example:2283"
        )
    finally:
        await engine.dispose()

    assert set(unlinked_ids) == {"clip-unlinked", "clip-stale"}
    assert "clip-current" not in unlinked_ids


@pytest.mark.asyncio
async def test_delete_clip_removes_only_managed_assets_and_record(tmp_path) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    thumbnail_root = tmp_path / "thumbnails"
    media = clip_root / "TV Shows" / "Pilot.mp4"
    thumbnail = thumbnail_root / "clip-one.jpg"
    source = tmp_path / "source" / "Pilot.mkv"
    media.parent.mkdir(parents=True)
    thumbnail.parent.mkdir(parents=True)
    source.parent.mkdir(parents=True)
    media.write_bytes(b"managed clip")
    thumbnail.write_bytes(b"thumbnail")
    source.write_bytes(b"original source")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    try:
        payload = clip_payload(media)
        payload["source_path"] = str(source)
        await insert_clip(engine, payload)
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE clips SET thumbnail_path = :path WHERE id = 'clip-one'"),
                {"path": str(thumbnail)},
            )

        result = await delete_clip(
            engine,
            "clip-one",
            1,
            clip_root=clip_root,
            thumbnail_root=thumbnail_root,
            run_blocking=run_blocking,
        )
        remaining = await _scalar(engine, "SELECT COUNT(*) FROM clips")
    finally:
        await engine.dispose()

    assert result is not None and result.deleted
    assert not media.exists()
    assert not thumbnail.exists()
    assert source.read_bytes() == b"original source"
    assert remaining == 0


@pytest.mark.asyncio
async def test_delete_clip_rejects_outside_derived_path_before_removing_media(
    tmp_path,
) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    thumbnail_root = tmp_path / "thumbnails"
    media = clip_root / "Movies" / "Film.mp4"
    outside = tmp_path / "source" / "do-not-delete.jpg"
    media.parent.mkdir(parents=True)
    outside.parent.mkdir(parents=True)
    media.write_bytes(b"managed clip")
    outside.write_bytes(b"source asset")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    try:
        await insert_clip(engine, clip_payload(media))
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE clips SET thumbnail_path = :path WHERE id = 'clip-one'"),
                {"path": str(outside)},
            )
        with pytest.raises(ClipDeleteSafetyError):
            await delete_clip(
                engine,
                "clip-one",
                1,
                clip_root=clip_root,
                thumbnail_root=thumbnail_root,
                run_blocking=run_blocking,
            )
        remaining = await _scalar(engine, "SELECT COUNT(*) FROM clips")
    finally:
        await engine.dispose()

    assert media.exists()
    assert outside.read_bytes() == b"source asset"
    assert remaining == 1


def test_metadata_plan_restores_automatic_title_and_resolves_collision(tmp_path) -> None:
    clip_root = tmp_path / "clips"
    source = clip_root / "Old" / "Custom.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"old")
    collision = clip_root / "TV Shows" / "Example Show - S01E01 - Pilot.mp4"
    collision.parent.mkdir(parents=True)
    collision.write_bytes(b"other")
    row = clip_payload(source, title="My custom title")
    row["custom_title"] = "My custom title"

    plan = build_metadata_edit_plan(
        row,
        ClipMetadataUpdate(
            expected_revision=1,
            custom_title=None,
            library="TV Shows",
        ),
        clip_root,
    )

    assert plan.proposed["title"] == "Example Show - S01E01 - Pilot"
    assert Path(plan.destination).name == "Example Show - S01E01 - Pilot - 2.mp4"
    assert plan.proposed["revision"] == 2


@pytest.mark.asyncio
async def test_metadata_edit_job_moves_file_records_history_and_rejects_stale_plan(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / "Pilot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"old clip")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    async def fake_rewrite(_source, output, metadata, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        envelope = json.dumps(
            {
                "application": "MediaClipMakarr",
                "schemaVersion": 4,
                "clipId": metadata["id"],
                "revision": metadata["revision"],
            }
        )
        output.write_bytes(b"new clip MediaClipMakarr " + envelope.encode())

    monkeypatch.setattr(runner_module, "rewrite_clip_metadata", fake_rewrite)
    try:
        await insert_clip(engine, clip_payload(source))
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        first_plan = build_metadata_edit_plan(
            row,
            ClipMetadataUpdate(
                expected_revision=1,
                custom_title="Renamed",
                library="Favorites",
            ),
            clip_root,
        )
        stale_plan = build_metadata_edit_plan(
            row,
            ClipMetadataUpdate(expected_revision=1, custom_title="Stale"),
            clip_root,
        )
        queued = await enqueue_metadata_edit_job(engine, first_plan)
        claimed = await claim_next_job(engine, "run-one")
        assert claimed is not None
        runner = JobRunner(
            engine,
            Settings(
                _env_file=None,
                work_dir=tmp_path / "work",
                clip_dir=clip_root,
                thumbnail_dir=tmp_path / "thumbnails",
            ),
            run_blocking=run_blocking,
            events=JobEventBroker(),
        )
        await runner._execute_claimed_job(claimed)
        edited = await get_clip(engine, "clip-one", clip_root)
        snapshot = await get_job_snapshot(engine, queued.id)
        history_count = await _scalar(
            engine, "SELECT COUNT(*) FROM clip_revisions WHERE clip_id = 'clip-one'"
        )
        await enqueue_metadata_edit_job(engine, stale_plan)
        thumbnail_claim = await claim_next_job(engine, "run-thumbnail")
        assert thumbnail_claim is not None and thumbnail_claim.type == "thumbnail_generate"
        await fail_job(
            engine,
            thumbnail_claim.id,
            thumbnail_claim.run_token,
            code="TEST_SKIPPED",
            message="Thumbnail is outside this metadata test.",
        )
        stale_claim = await claim_next_job(engine, "run-two")
        assert stale_claim is not None
        with pytest.raises(ClipRevisionConflict):
            await runner._execute_claimed_job(stale_claim)
    finally:
        await engine.dispose()

    assert edited is not None
    assert edited["revision"] == 2
    assert Path(str(edited["file_path"])).name == "Renamed.mp4"
    assert Path(str(edited["file_path"])).parent.name == "Favorites"
    assert not source.exists()
    assert snapshot is not None and snapshot.state == "SUCCEEDED"
    assert history_count == 2


@pytest.mark.asyncio
async def test_pending_metadata_edit_recovers_before_install_boundary(tmp_path) -> None:
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / "Pilot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"old clip")
    temp = tmp_path / "work" / "jobs" / "edit" / "metadata.mp4"
    temp.parent.mkdir(parents=True)
    temp.write_bytes(
        b'new clip MediaClipMakarr {"application":"MediaClipMakarr","schemaVersion":4,'
        b'"clipId":"clip-one","revision":2}'
    )
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    try:
        await insert_clip(engine, clip_payload(source))
        row = await get_clip(engine, "clip-one", clip_root)
        assert row is not None
        plan = build_metadata_edit_plan(
            row,
            ClipMetadataUpdate(expected_revision=1, custom_title="Recovered"),
            clip_root,
        )
        await enqueue_metadata_edit_job(engine, plan)
        claimed = await claim_next_job(engine, "run-token")
        assert claimed is not None
        proposed = {
            **row,
            **plan.proposed,
            "file_path": plan.destination,
            "updated_at": datetime.now(UTC).replace(tzinfo=None),
            "thumbnail_path": None,
            "thumbnail_source_size": None,
            "thumbnail_source_modified_ns": None,
            "file_size_bytes": temp.stat().st_size,
            "file_modified_ns": temp.stat().st_mtime_ns,
        }
        await transition_to_finalizing(
            engine,
            plan.job_id,
            "run-token",
            clip_id=plan.clip_id,
            revision=2,
            destination=Path(plan.destination),
            render_plan_hash=plan.operation_hash,
        )
        await create_pending_metadata_operation(
            engine,
            job_id=plan.job_id,
            clip_id=plan.clip_id,
            temp_path=temp,
            source_path=source,
            destination=Path(plan.destination),
            expected_revision=1,
            operation_hash=plan.operation_hash,
            clip=proposed,
        )

        recovered = await recover_finalizing_jobs(engine, run_blocking)
        clip = await get_clip(engine, "clip-one", clip_root)
        snapshot = await get_job_snapshot(engine, plan.job_id)
    finally:
        await engine.dispose()

    assert recovered == [plan.job_id]
    assert clip is not None and clip["revision"] == 2
    assert snapshot is not None and snapshot.state == "SUCCEEDED"
    recovered_bytes = await asyncio.to_thread(Path(plan.destination).read_bytes)
    assert recovered_bytes.startswith(b"new clip")
    assert not source.exists()


def test_media_endpoint_supports_ranges_and_rejects_outside_database_paths(
    tmp_path, monkeypatch
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    settings = Settings(
        _env_file=None,
        private_data_dir=tmp_path / "private",
        work_dir=tmp_path / "work",
        clip_dir=tmp_path / "clips",
        thumbnail_dir=tmp_path / "thumbnails",
        source_dirs=[source_root],
        frontend_dist_dir=tmp_path / "missing-frontend",
    )

    async def healthy_media_tools(_settings):
        return MediaToolInspection(
            status="ok", message="ready", details={"identity_ok": True}
        )

    monkeypatch.setattr(main_module, "inspect_media_tools", healthy_media_tools)
    with TestClient(main_module.create_app(settings)) as client:
        managed = settings.resolved_clip_dir / "Movies" / "Range.mp4"
        managed.parent.mkdir(parents=True)
        managed.write_bytes(b"0123456789")
        outside = source_root / "private.mp4"
        outside.write_bytes(b"source secret")
        client.portal.call(
            insert_clip,
            client.app.state.database_engine,
            clip_payload(managed, clip_id="managed", title="Range"),
        )
        client.portal.call(
            insert_clip,
            client.app.state.database_engine,
            clip_payload(outside, clip_id="outside", title="Outside"),
        )

        all_response = client.get("/api/clips?all=true&page=2")
        response = client.get("/api/clips/managed/media", headers={"Range": "bytes=2-5"})
        rejected = client.get("/api/clips/outside/media")

    assert all_response.status_code == 200
    assert all_response.json()["page"] == 1
    assert all_response.json()["total"] == 2
    assert len(all_response.json()["items"]) == 2
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert rejected.status_code == 404


async def _scalar(engine, statement: str) -> int:
    async with engine.connect() as connection:
        return int(await connection.scalar(text(statement)) or 0)
