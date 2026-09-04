from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

import mediaclipmakarr.jobs.runner as runner_module
from mediaclipmakarr.clip_edits import ClipEditError, ClipTrimSaveRequest, build_trim_render_plan
from mediaclipmakarr.clips import get_clip, insert_clip
from mediaclipmakarr.config import Settings
from mediaclipmakarr.database import create_database_engine, upgrade_database
from mediaclipmakarr.hdr import VideoColorMetadata
from mediaclipmakarr.jobs import (
    JobEventBroker,
    JobRunner,
    enqueue_clip_create_job,
    get_job_snapshot,
)
from mediaclipmakarr.media_renderer import RenderedClipFile
from mediaclipmakarr.source_media import (
    MediaStreamIdentity,
    ResolvedSourceMedia,
    SourceFingerprint,
    VideoStreamIdentity,
)


async def run_blocking(function, *args):
    return function(*args)


def managed_source(path: Path) -> ResolvedSourceMedia:
    stat = path.stat()
    audio = MediaStreamIdentity(stream_index=1, codec_type="audio", codec_name="aac")
    return ResolvedSourceMedia(
        plex_path=str(path),
        local_path=str(path),
        fingerprint=SourceFingerprint(
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
        ),
        duration_ms=10_000,
        video_streams=[
            VideoStreamIdentity(
                stream_index=0,
                codec_type="video",
                codec_name="h264",
                width=1280,
                height=720,
                color=VideoColorMetadata(color_transfer="bt709"),
            )
        ],
        audio_streams=[audio],
        subtitle_streams=[],
        selected_audio_stream=audio,
    )


def parent_payload(path: Path) -> dict[str, object]:
    stat = path.stat()
    created = datetime(2026, 9, 1, 12, 0)
    return {
        "id": "clip-parent",
        "title": "Example",
        "library": "Movies",
        "media_type": "movie",
        "file_path": str(path),
        "duration_ms": 10_000,
        "revision": 3,
        "source_start_ms": 50_000,
        "source_end_ms": 60_000,
        "source_path": "/plex/Example.mkv",
        "source_size_bytes": 999,
        "source_modified_at": datetime(2026, 8, 1, 12, 0),
        "selected_audio_stream_index": 4,
        "render_plan_hash": "parent-render",
        "created_at": created,
        "updated_at": created,
        "automatic_title": "Example",
        "file_size_bytes": stat.st_size,
        "file_modified_ns": stat.st_mtime_ns,
    }


def trim_plan(
    parent: dict[str, object], path: Path, mode: str = "new"
):
    return build_trim_render_plan(
        parent,
        ClipTrimSaveRequest(
            start_ms=1_250,
            end_ms=7_500,
            expected_revision=3,
            mode=mode,
        ),
        managed_source(path),
        path.stat(),
        x264_preset="veryfast",
    )


def test_trim_plan_translates_original_source_range_and_records_direct_parent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "Example.mp4"
    path.write_bytes(b"managed clip")
    parent = parent_payload(path)

    plan = trim_plan(parent, path)

    assert plan.operation == "trim_new"
    assert plan.clip_id != parent["id"]
    assert plan.parent_clip_id == parent["id"]
    assert (plan.source_start_ms, plan.source_end_ms) == (1_250, 7_500)
    assert (plan.provenance_start_ms, plan.provenance_end_ms) == (51_250, 57_500)
    assert plan.provenance_source_path == "/plex/Example.mkv"
    assert parent["revision"] == 3


def test_replace_plan_preserves_identity_and_rejects_stale_revision(tmp_path: Path) -> None:
    path = tmp_path / "Example.mp4"
    path.write_bytes(b"managed clip")
    parent = parent_payload(path)

    plan = trim_plan(parent, path, "replace")

    assert plan.operation == "trim_replace"
    assert plan.clip_id == parent["id"]
    assert plan.revision == 4
    assert plan.clip_created_at == datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    with pytest.raises(ClipEditError, match="stale"):
        build_trim_render_plan(
            parent,
            ClipTrimSaveRequest(
                start_ms=1,
                end_ms=2,
                expected_revision=2,
                mode="replace",
            ),
            managed_source(path),
            path.stat(),
            x264_preset="veryfast",
        )


async def wait_for_terminal_job(engine, job_id: str):
    for _ in range(100):
        snapshot = await get_job_snapshot(engine, job_id)
        if snapshot is not None and snapshot.state in {"SUCCEEDED", "FAILED"}:
            return snapshot
        await asyncio.sleep(0.02)
    raise AssertionError("Trim job did not complete.")


@pytest.mark.asyncio
async def test_replace_failure_leaves_existing_media_and_revision_unchanged(tmp_path: Path) -> None:
    database = tmp_path / "application.db"
    clip_dir = tmp_path / "clips"
    source = clip_dir / "Movies" / "Example.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original bytes")
    upgrade_database(database)
    engine = create_database_engine(database)
    await insert_clip(engine, parent_payload(source))
    plan = trim_plan(parent_payload(source), source, "replace")
    queued = await enqueue_clip_create_job(engine, plan)

    async def failing_renderer(*_args, **_kwargs):
        raise RuntimeError("render failed")

    runner = JobRunner(
        engine,
        Settings(
            _env_file=None,
            clip_dir=clip_dir,
            work_dir=tmp_path / "work",
            thumbnail_dir=tmp_path / "thumbs",
        ),
        run_blocking=run_blocking,
        events=JobEventBroker(),
        renderer=failing_renderer,
    )
    try:
        await runner.start()
        runner.wake()
        result = await wait_for_terminal_job(engine, queued.id)
        stored = await get_clip(engine, "clip-parent", clip_dir)
    finally:
        await runner.stop()
        await engine.dispose()

    assert result.state == "FAILED"
    assert source.read_bytes() == b"original bytes"
    assert stored is not None and stored["revision"] == 3


@pytest.mark.asyncio
async def test_replace_installs_validated_output_and_advances_revision(
    monkeypatch, tmp_path: Path
) -> None:
    database = tmp_path / "application.db"
    clip_dir = tmp_path / "clips"
    source = clip_dir / "Movies" / "Example.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original bytes")
    upgrade_database(database)
    engine = create_database_engine(database)
    parent = parent_payload(source)
    parent["thumbnail_path"] = str(tmp_path / "thumbs" / "clip-parent.jpg")
    parent["thumbnail_source_size"] = source.stat().st_size
    parent["thumbnail_source_modified_ns"] = source.stat().st_mtime_ns
    await insert_clip(engine, parent)
    plan = trim_plan(parent, source, "replace")
    queued = await enqueue_clip_create_job(engine, plan)

    async def renderer(plan, settings, *, progress):
        await progress(1, "rendered")
        output = settings.resolved_work_dir / "jobs" / plan.job_id / "rendered.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"validated replacement")
        return RenderedClipFile(path=output, duration_ms=6_250)

    async def accept_output(*_args, **_kwargs):
        return None

    monkeypatch.setattr(runner_module, "validate_trim_rendered_output", accept_output)
    runner = JobRunner(
        engine,
        Settings(
            _env_file=None,
            clip_dir=clip_dir,
            work_dir=tmp_path / "work",
            thumbnail_dir=tmp_path / "thumbs",
        ),
        run_blocking=run_blocking,
        events=JobEventBroker(),
        renderer=renderer,
    )
    try:
        await runner.start()
        runner.wake()
        result = await wait_for_terminal_job(engine, queued.id)
        stored = await get_clip(engine, "clip-parent", clip_dir)
    finally:
        await runner.stop()
        await engine.dispose()

    assert result.state == "SUCCEEDED"
    assert source.read_bytes() == b"validated replacement"
    assert stored is not None
    assert stored["revision"] == 4
    assert stored["duration_ms"] == 6_250
    assert (stored["source_start_ms"], stored["source_end_ms"]) == (51_250, 57_500)
    assert stored["thumbnail_path"] is None
