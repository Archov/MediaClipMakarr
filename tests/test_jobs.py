from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

import mediaclipmakarr.jobs as jobs_module
import mediaclipmakarr.media_renderer as media_renderer_module
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
from mediaclipmakarr.media_renderer import (
    PreparedTextSubtitle,
    RenderedClipFile,
    build_ffmpeg_clip_args,
    render_clip_file,
)
from mediaclipmakarr.plex import PlexSession
from mediaclipmakarr.render_plan import build_clip_render_plan
from mediaclipmakarr.source_media import (
    MediaStreamIdentity,
    ResolvedSourceMedia,
    SourceFingerprint,
    SubtitleSelection,
    VideoColorMetadata,
    VideoStreamIdentity,
)
from mediaclipmakarr.subprocesses import CommandResult


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


async def run_blocking(function, *args):
    return function(*args)


async def wait_for_job_state(engine, job_id: str, state: str):
    for _ in range(50):
        snapshot = await get_job_snapshot(engine, job_id)
        if snapshot is not None and snapshot.state == state:
            return snapshot
        await asyncio.sleep(0.02)
    raise AssertionError(f"Job {job_id} did not reach {state}.")


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


def test_ffmpeg_args_burn_text_subtitles_with_preroll_and_exact_trim(tmp_path) -> None:
    source_file = tmp_path / "Movie.mkv"
    source_file.write_bytes(b"media")
    media = source_media(source_file).model_copy(
        update={
            "subtitle_streams": [
                MediaStreamIdentity(
                    stream_index=3,
                    codec_type="subtitle",
                    codec_name="subrip",
                    language="eng",
                )
            ],
            "attachment_streams": [
                MediaStreamIdentity(
                    stream_index=4,
                    codec_type="attachment",
                    codec_name="ttf",
                    title="Example.ttf",
                )
            ],
            "selected_subtitle": SubtitleSelection(
                enabled=True,
                stream=MediaStreamIdentity(
                    stream_index=3,
                    codec_type="subtitle",
                    codec_name="subrip",
                    language="eng",
                ),
                strategy="embedded_text",
            ),
            "subtitles_forced_off": False,
        }
    )
    plan = build_clip_render_plan(
        session=session(),
        request=request_range(),
        source_media=media,
        x264_preset="veryfast",
    )

    argv = build_ffmpeg_clip_args(
        plan,
        Settings(_env_file=None, ffmpeg_path=Path("ffmpeg-test")),
        tmp_path / "out.mp4",
        subtitle_preroll_ms=1_000,
        prepared_text_subtitle=PreparedTextSubtitle(
            path=tmp_path / "job" / "subtitles" / "selected-subtitle.ass",
            fonts_dir=tmp_path / "job" / "fonts",
        ),
    )

    assert ["-ss", "0.000"] == argv[argv.index("-ss") : argv.index("-ss") + 2]
    assert ["-t", "4.000"] == argv[argv.index("-t") : argv.index("-t") + 2]
    vf = argv[argv.index("-vf") + 1]
    assert "subtitles=" in vf
    assert "filename=" in vf
    assert "selected-subtitle.ass" in vf
    assert ":si=" not in vf
    assert "trim=start=1.000:duration=3.000" in vf
    assert ["-af", "atrim=start=1.000:duration=3.000,asetpts=PTS-STARTPTS"] == argv[
        argv.index("-af") : argv.index("-af") + 2
    ]


@pytest.mark.asyncio
async def test_embedded_text_subtitle_is_prepared_before_libass_render(
    monkeypatch, tmp_path
) -> None:
    source_dir = tmp_path / "Young Ladies Don't Play Fighting Games"
    source_dir.mkdir()
    source_file = source_dir / "Episode 01.mkv"
    source_file.write_bytes(b"media")
    media = source_media(source_file).model_copy(
        update={
            "subtitle_streams": [
                MediaStreamIdentity(
                    stream_index=2,
                    codec_type="subtitle",
                    codec_name="subrip",
                    language="eng",
                ),
                MediaStreamIdentity(
                    stream_index=3,
                    codec_type="subtitle",
                    codec_name="ass",
                    language="jpn",
                ),
            ],
            "attachment_streams": [
                MediaStreamIdentity(
                    stream_index=4,
                    codec_type="attachment",
                    codec_name="ttf",
                    title="Show Font.ttf",
                )
            ],
            "selected_subtitle": SubtitleSelection(
                enabled=True,
                stream=MediaStreamIdentity(
                    stream_index=3,
                    codec_type="subtitle",
                    codec_name="ass",
                    language="jpn",
                ),
                strategy="embedded_text",
            ),
            "subtitles_forced_off": False,
        }
    )
    plan = build_clip_render_plan(
        session=session(),
        request=request_range(),
        source_media=media,
        x264_preset="veryfast",
    )
    settings = Settings(
        _env_file=None,
        work_dir=tmp_path / "work",
        ffmpeg_path=Path("ffmpeg-test"),
        subprocess_timeout_seconds=7,
        media_preparation_timeout_seconds=180,
    )
    commands: list[tuple[str, ...]] = []
    render_argv: list[str] | None = None
    render_cwd: Path | None = None

    async def fake_run_command(argv, **kwargs):
        normalized = tuple(str(value) for value in argv)
        commands.append(normalized)
        assert kwargs["timeout_seconds"] == 180
        return CommandResult(normalized, 0, "", "")

    async def fake_render(argv, *, duration_ms, progress, cwd=None):
        nonlocal render_argv, render_cwd
        render_argv = [str(value) for value in argv]
        render_cwd = cwd
        await progress(1.0, "rendered")

    monkeypatch.setattr(media_renderer_module, "run_command", fake_run_command)
    monkeypatch.setattr(media_renderer_module, "_run_ffmpeg_with_progress", fake_render)

    rendered = await render_clip_file(
        plan,
        settings,
        progress=lambda _progress, _message: asyncio.sleep(0),
    )

    assert rendered.duration_ms == 3000
    assert len(commands) == 2
    subtitle_extract = commands[0]
    assert subtitle_extract[subtitle_extract.index("-i") + 1] == str(source_file.resolve())
    assert ["-map", "0:3"] == list(subtitle_extract)[
        subtitle_extract.index("-map") : subtitle_extract.index("-map") + 2
    ]
    assert subtitle_extract[-1].endswith("selected-subtitle.ass")
    font_extract = commands[1]
    assert "-dump_attachment:4" in font_extract

    assert render_argv is not None
    assert render_cwd == settings.resolved_work_dir / "jobs" / plan.job_id
    vf = render_argv[render_argv.index("-vf") + 1]
    subtitle_filter = next(part for part in vf.split(",") if part.startswith("subtitles="))
    assert "Young Ladies" not in subtitle_filter
    assert "filename='subtitles/selected-subtitle.ass'" in subtitle_filter
    assert ":si=" not in subtitle_filter
    assert "fontsdir='fonts'" in subtitle_filter


def test_ffmpeg_args_overlay_bitmap_subtitles_after_packet_preroll(tmp_path) -> None:
    source_file = tmp_path / "Movie.mkv"
    source_file.write_bytes(b"media")
    media = source_media(source_file).model_copy(
        update={
            "selected_subtitle": SubtitleSelection(
                enabled=True,
                stream=MediaStreamIdentity(
                    stream_index=4,
                    codec_type="subtitle",
                    codec_name="hdmv_pgs_subtitle",
                    language="eng",
                ),
                strategy="bitmap",
            ),
            "subtitles_forced_off": False,
        }
    )
    plan = build_clip_render_plan(
        session=session(),
        request=request_range(),
        source_media=media,
        x264_preset="veryfast",
    )

    argv = build_ffmpeg_clip_args(
        plan,
        Settings(_env_file=None, ffmpeg_path=Path("ffmpeg-test")),
        tmp_path / "out.mp4",
        subtitle_preroll_ms=500,
    )

    assert ["-ss", "0.500"] == argv[argv.index("-ss") : argv.index("-ss") + 2]
    assert "-filter_complex" in argv
    filter_complex = argv[argv.index("-filter_complex") + 1]
    assert "[0:4]setpts=PTS-STARTPTS[s]" in filter_complex
    assert "[v][s]overlay" in filter_complex
    assert "trim=start=0.500:duration=3.000" in filter_complex
    assert "[0:1]atrim=start=0.500:duration=3.000,asetpts=PTS-STARTPTS[outa]" in filter_complex
    assert ["-map", "[outv]"] == argv[argv.index("-map") : argv.index("-map") + 2]
    second_map = argv.index("-map", argv.index("-map") + 1)
    assert ["-map", "[outa]"] == argv[second_map : second_map + 2]


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


@pytest.mark.asyncio
async def test_runner_survives_claim_failure_and_continues(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "application.db"
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    calls = 0
    logged_messages: list[str] = []

    async def fail_then_stop(_engine, _run_token):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary sqlite failure")
        runner._stopping = True
        runner._wake.set()
        return None

    async def no_sleep(_seconds):
        return None

    try:
        runner = JobRunner(
            engine,
            Settings(_env_file=None),
            run_blocking=run_blocking,
            events=JobEventBroker(),
        )
        monkeypatch.setattr(jobs_module, "claim_next_job", fail_then_stop)
        monkeypatch.setattr(jobs_module.asyncio, "sleep", no_sleep)
        monkeypatch.setattr(
            jobs_module.logger,
            "exception",
            lambda message, *args, **kwargs: logged_messages.append(message),
        )

        await runner._run()
    finally:
        await engine.dispose()

    assert calls == 2
    assert logged_messages == ["The job runner could not claim the next queued job."]


@pytest.mark.asyncio
async def test_render_progress_is_live_but_sqlite_persistence_is_throttled(
    monkeypatch, tmp_path
) -> None:
    class RecordingEvents(JobEventBroker):
        def __init__(self) -> None:
            super().__init__()
            self.snapshots = []

        async def publish(self, job_id, snapshot=None):
            if snapshot is not None:
                self.snapshots.append(snapshot)
            await super().publish(job_id, snapshot)

    database_path = tmp_path / "application.db"
    source_file = tmp_path / "Movie.mkv"
    source_file.write_bytes(b"media")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    monotonic_values = [100.0, 100.1]

    async def renderer(_plan, _settings, *, progress):
        await progress(0.10, "first progress")
        await progress(0.20, "second progress")
        raise RuntimeError("stop before finalization")

    try:
        settings = Settings(_env_file=None, work_dir=tmp_path / "work", clip_dir=tmp_path / "clips")
        events = RecordingEvents()
        runner = JobRunner(
            engine,
            settings,
            run_blocking=run_blocking,
            events=events,
            renderer=renderer,
            progress_persist_interval_seconds=10.0,
        )
        def fake_monotonic():
            return monotonic_values.pop(0) if monotonic_values else 100.2

        monkeypatch.setattr(jobs_module.time, "monotonic", fake_monotonic)
        plan = build_clip_render_plan(
            session=session(),
            request=request_range(),
            source_media=source_media(source_file),
            x264_preset="veryfast",
        )
        queued = await enqueue_clip_create_job(engine, plan)
        claimed = await claim_next_job(engine, "run-token")
        assert claimed is not None

        with pytest.raises(RuntimeError, match="stop before finalization"):
            await runner._execute_claimed_job(claimed)

        durable = await get_job_snapshot(engine, queued.id)
    finally:
        await engine.dispose()

    assert durable is not None
    assert durable.stage == "rendering"
    assert durable.current_stage_progress == pytest.approx(0.10)
    assert events.snapshots[-1].stage == "rendering"
    assert events.snapshots[-1].current_stage_progress == pytest.approx(0.20)


@pytest.mark.asyncio
async def test_graceful_shutdown_fails_running_job_with_app_shutdown(tmp_path) -> None:
    database_path = tmp_path / "application.db"
    source_file = tmp_path / "Movie.mkv"
    source_file.write_bytes(b"media")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    renderer_started = asyncio.Event()

    async def renderer(_plan, _settings, *, progress):
        renderer_started.set()
        await asyncio.Event().wait()
        raise AssertionError("renderer should be cancelled")

    try:
        settings = Settings(_env_file=None, work_dir=tmp_path / "work", clip_dir=tmp_path / "clips")
        runner = JobRunner(
            engine,
            settings,
            run_blocking=run_blocking,
            events=JobEventBroker(),
            renderer=renderer,
        )
        plan = build_clip_render_plan(
            session=session(),
            request=request_range(),
            source_media=source_media(source_file),
            x264_preset="veryfast",
        )
        await runner.start()
        queued = await enqueue_clip_create_job(engine, plan)
        runner.wake()
        await asyncio.wait_for(renderer_started.wait(), timeout=1.0)
        await runner.stop()
        snapshot = await get_job_snapshot(engine, queued.id)
    finally:
        await engine.dispose()

    assert snapshot is not None
    assert snapshot.state == "FAILED"
    assert snapshot.error is not None
    assert snapshot.error.code == "APP_SHUTDOWN"


@pytest.mark.asyncio
async def test_queued_job_is_preserved_across_shutdown_and_runs_after_restart(tmp_path) -> None:
    database_path = tmp_path / "application.db"
    source_file = tmp_path / "Movie.mkv"
    source_file.write_bytes(b"media")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    rendered = asyncio.Event()

    async def renderer(plan, settings, *, progress):
        await progress(1.0, "rendered")
        output = settings.resolved_work_dir / "jobs" / plan.job_id / "rendered.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered mp4")
        rendered.set()
        return RenderedClipFile(path=output, duration_ms=plan.source_end_ms - plan.source_start_ms)

    try:
        settings = Settings(_env_file=None, work_dir=tmp_path / "work", clip_dir=tmp_path / "clips")
        plan = build_clip_render_plan(
            session=session(),
            request=request_range(),
            source_media=source_media(source_file),
            x264_preset="veryfast",
        )
        first_runner = JobRunner(
            engine,
            settings,
            run_blocking=run_blocking,
            events=JobEventBroker(),
            renderer=renderer,
        )
        await first_runner.start()
        queued = await enqueue_clip_create_job(engine, plan)
        await first_runner.stop()
        preserved = await get_job_snapshot(engine, queued.id)

        second_runner = JobRunner(
            engine,
            settings,
            run_blocking=run_blocking,
            events=JobEventBroker(),
            renderer=renderer,
        )
        await second_runner.start()
        second_runner.wake()
        await asyncio.wait_for(rendered.wait(), timeout=1.0)
        completed = await wait_for_job_state(engine, queued.id, "SUCCEEDED")
        await second_runner.stop()
    finally:
        await engine.dispose()

    assert preserved is not None
    assert preserved.state == "QUEUED"
    assert completed.state == "SUCCEEDED"


@pytest.mark.asyncio
async def test_restart_marks_abandoned_running_job_failed(tmp_path) -> None:
    database_path = tmp_path / "application.db"
    source_file = tmp_path / "Movie.mkv"
    source_file.write_bytes(b"media")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)

    try:
        settings = Settings(_env_file=None, work_dir=tmp_path / "work", clip_dir=tmp_path / "clips")
        plan = build_clip_render_plan(
            session=session(),
            request=request_range(),
            source_media=source_media(source_file),
            x264_preset="veryfast",
        )
        queued = await enqueue_clip_create_job(engine, plan)
        claimed = await claim_next_job(engine, "abandoned-run-token")
        assert claimed is not None

        runner = JobRunner(
            engine,
            settings,
            run_blocking=run_blocking,
            events=JobEventBroker(),
        )
        await runner.start()
        snapshot = await get_job_snapshot(engine, queued.id)
        await runner.stop()
    finally:
        await engine.dispose()

    assert snapshot is not None
    assert snapshot.state == "FAILED"
    assert snapshot.error is not None
    assert snapshot.error.code == "APP_RESTARTED"
