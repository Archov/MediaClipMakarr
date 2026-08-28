from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mediaclipmakarr.config import Settings
from mediaclipmakarr.database import create_database_engine, upgrade_database
from mediaclipmakarr.jobs import (
    JobEventBroker,
    JobRunner,
    claim_next_job,
    create_pending_operation,
    enqueue_clip_create_job,
    get_clip,
    get_job_snapshot,
    recover_finalizing_jobs,
    transition_to_finalizing,
    update_running_job,
)
from mediaclipmakarr.media_renderer import RenderedClipFile, build_ffmpeg_clip_args
from mediaclipmakarr.plex import PlexSession
from mediaclipmakarr.render_plan import build_clip_render_plan
from mediaclipmakarr.source_media import (
    MediaStreamIdentity,
    ResolvedSourceMedia,
    SourceFingerprint,
    VideoColorMetadata,
    VideoStreamIdentity,
)


def source_media(source_file: Path) -> ResolvedSourceMedia:
    return ResolvedSourceMedia(
        plex_path="/plex/Movie.mkv",
        local_path=str(source_file.resolve()),
        fingerprint=SourceFingerprint(
            size_bytes=source_file.stat().st_size,
            modified_at=datetime.fromtimestamp(source_file.stat().st_mtime, UTC),
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
        audio_streams=[
            MediaStreamIdentity(stream_index=1, codec_type="audio", codec_name="aac")
        ],
        subtitle_streams=[],
        selected_audio_stream=MediaStreamIdentity(
            stream_index=1, codec_type="audio", codec_name="aac"
        ),
    )


def session() -> PlexSession:
    return PlexSession(
        session_identity="plex-session:living-room",
        media_identity="plex-media:movie",
        title="A Movie",
        media_type="movie",
        plex_user="Alice",
        player="Living Room",
        state="playing",
        position_ms=1_000,
        duration_ms=10_000,
        sampled_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        plex_rating_key="501",
        plex_media_key="media-501",
        plex_part_id="part-501",
        plex_part_key="/library/parts/501",
        plex_part_file="/plex/Movie.mkv",
    )


def request_range():
    from mediaclipmakarr.clips import ClipCreateRequest

    return ClipCreateRequest(
        session_identity="plex-session:living-room",
        media_identity="plex-media:movie",
        start_ms=1000,
        end_ms=4000,
    )


@pytest.mark.asyncio
async def test_claim_next_job_is_atomic_and_rejects_stale_token(tmp_path) -> None:
    database_path = tmp_path / "application.db"
    source_file = tmp_path / "Movie.mkv"
    source_file.write_bytes(b"media")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    try:
        plan = build_clip_render_plan(
            session=session(),
            request=request_range(),
            source_media=source_media(source_file),
            x264_preset="veryfast",
        )
        queued = await enqueue_clip_create_job(engine, plan)
        claimed = await claim_next_job(engine, "run-token")
        duplicate = await claim_next_job(engine, "other-token")
        await update_running_job(
            engine,
            queued.id,
            "wrong-token",
            stage="rendering",
            progress=0.5,
            current_stage_progress=0.5,
            message="wrong token",
        )
    except Exception as error:
        observed = error
    else:
        observed = None
    finally:
        await engine.dispose()

    assert claimed is not None
    assert claimed.id == queued.id
    assert duplicate is None
    assert type(observed).__name__ == "JobUpdateConflict"


@pytest.mark.asyncio
async def test_job_runner_finalizes_clip_and_serves_by_managed_id(tmp_path) -> None:
    database_path = tmp_path / "application.db"
    source_file = tmp_path / "Movie.mkv"
    source_file.write_bytes(b"media")
    clip_dir = tmp_path / "clips"
    work_dir = tmp_path / "work"
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    async def run_blocking(function, *args):
        return function(*args)

    async def renderer(plan, settings, *, progress):
        await progress(1.0, "rendered")
        output = settings.resolved_work_dir / "jobs" / plan.job_id / "rendered.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered mp4")
        return RenderedClipFile(path=output, duration_ms=plan.source_end_ms - plan.source_start_ms)

    try:
        settings = Settings(
            _env_file=None,
            private_data_dir=tmp_path / "private",
            work_dir=work_dir,
            clip_dir=clip_dir,
            source_dirs=[tmp_path],
        )
        events = JobEventBroker()
        runner = JobRunner(
            engine,
            settings,
            run_blocking=run_blocking,
            events=events,
            renderer=renderer,
        )
        plan = build_clip_render_plan(
            session=session(),
            request=request_range(),
            source_media=source_media(source_file),
            x264_preset="veryfast",
        )
        queued = await enqueue_clip_create_job(engine, plan)
        claimed = await claim_next_job(engine, "run-token")
        assert claimed is not None
        await runner._execute_claimed_job(claimed)
        snapshot = await get_job_snapshot(engine, queued.id)
        clip = await get_clip(engine, plan.clip_id, clip_dir)
    finally:
        await engine.dispose()

    assert snapshot is not None
    assert snapshot.state == "SUCCEEDED"
    assert snapshot.result is not None
    assert snapshot.result["clip_id"] == plan.clip_id
    assert clip is not None
    assert await asyncio.to_thread(Path(clip["file_path"]).read_bytes) == b"rendered mp4"


def test_ffmpeg_args_force_phase_one_output_contract(tmp_path) -> None:
    source_file = tmp_path / "Movie.mkv"
    source_file.write_bytes(b"media")
    plan = build_clip_render_plan(
        session=session(),
        request=request_range(),
        source_media=source_media(source_file),
        x264_preset="veryfast",
    )

    argv = build_ffmpeg_clip_args(
        plan,
        Settings(_env_file=None, ffmpeg_path=Path("ffmpeg-test")),
        tmp_path / "out.mp4",
    )

    assert argv[:2] == ["ffmpeg-test", "-hide_banner"]
    assert ["-map", "0:0"] == argv[argv.index("-map") : argv.index("-map") + 2]
    assert ["-map", "0:1"] == argv[argv.index("-map", argv.index("-map") + 1) :][0:2]
    assert "-sn" in argv
    assert ["-c:v", "libx264"] == argv[argv.index("-c:v") : argv.index("-c:v") + 2]
    assert ["-crf", "18"] == argv[argv.index("-crf") : argv.index("-crf") + 2]
    assert ["-c:a", "aac"] == argv[argv.index("-c:a") : argv.index("-c:a") + 2]
    assert ["-b:a", "192k"] == argv[argv.index("-b:a") : argv.index("-b:a") + 2]
    assert ["-movflags", "+faststart"] == argv[
        argv.index("-movflags") : argv.index("-movflags") + 2
    ]
    assert any(value.startswith("comment=MediaClipMakarr ") for value in argv)


@pytest.mark.asyncio
async def test_finalizing_job_recovers_pending_temp_install_after_restart(tmp_path) -> None:
    database_path = tmp_path / "application.db"
    source_file = tmp_path / "Movie.mkv"
    source_file.write_bytes(b"media")
    clip_dir = tmp_path / "clips"
    temp_path = tmp_path / "work" / "jobs" / "job" / "rendered.mp4"
    temp_path.parent.mkdir(parents=True)
    temp_path.write_bytes(b"rendered mp4")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    async def run_blocking(function, *args):
        return function(*args)

    try:
        plan = build_clip_render_plan(
            session=session(),
            request=request_range(),
            source_media=source_media(source_file),
            x264_preset="veryfast",
        )
        queued = await enqueue_clip_create_job(engine, plan)
        claimed = await claim_next_job(engine, "run-token")
        assert claimed is not None
        destination = clip_dir / "Movies" / "A Movie.mp4"
        clip = {
            "id": plan.clip_id,
            "title": plan.title,
            "library": plan.library,
            "media_type": plan.media_type,
            "file_path": str(destination),
            "duration_ms": 3000,
            "revision": 1,
            "source_start_ms": 1000,
            "source_end_ms": 4000,
            "source_path": plan.source_media.local_path,
            "source_size_bytes": plan.source_media.fingerprint.size_bytes,
            "source_modified_at": plan.source_media.fingerprint.modified_at.replace(tzinfo=None),
            "selected_audio_stream_index": 1,
            "render_plan_hash": plan.render_plan_hash,
            "created_at": datetime(2026, 8, 28, 12, 0),
            "updated_at": datetime(2026, 8, 28, 12, 0),
        }
        await transition_to_finalizing(
            engine,
            queued.id,
            "run-token",
            clip_id=plan.clip_id,
            revision=1,
            destination=destination,
            render_plan_hash=plan.render_plan_hash,
        )
        await create_pending_operation(
            engine,
            job_id=queued.id,
            plan=plan,
            rendered_path=temp_path,
            destination=destination,
            clip=clip,
        )

        recovered = await recover_finalizing_jobs(engine, run_blocking)
        snapshot = await get_job_snapshot(engine, queued.id)
        recovered_clip = await get_clip(engine, plan.clip_id, clip_dir)
    finally:
        await engine.dispose()

    assert recovered == [queued.id]
    assert snapshot is not None
    assert snapshot.state == "SUCCEEDED"
    assert recovered_clip is not None
    assert await asyncio.to_thread(destination.read_bytes) == b"rendered mp4"
