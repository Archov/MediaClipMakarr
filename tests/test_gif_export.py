from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from mediaclipmakarr.clip_library import (
    GIF_PROFILES,
    GifSizeLimitExceededError,
    build_gif_job_plan,
    generate_gif,
    gif_path,
    purge_gif_cache,
    stale_gif_paths,
)
from mediaclipmakarr.clips import get_clip, insert_clip
from mediaclipmakarr.config import Settings
from mediaclipmakarr.database import create_database_engine, upgrade_database
from mediaclipmakarr.jobs import (
    JobEventBroker,
    JobRunner,
    claim_next_job,
    enqueue_gif_job,
    get_job_snapshot,
)
from mediaclipmakarr.subprocesses import run_command


async def _make_motion_fixture(path: Path, ffmpeg: str) -> None:
    """A short synthetic clip with real per-frame motion (not a static test
    card) so palettegen/paletteuse have something to actually encode."""
    await run_command(
        [
            ffmpeg,
            "-hide_banner",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=24:duration=2",
            "-pix_fmt",
            "yuv420p",
            path,
        ],
        timeout_seconds=30,
    )


@pytest.mark.asyncio
async def test_generate_gif_produces_a_silent_looping_result_within_the_limit(
    tmp_path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg and ffprobe are required for the GIF export smoke test.")

    source = tmp_path / "source.mp4"
    await _make_motion_fixture(source, ffmpeg)
    output = tmp_path / "clip.gif"

    profile = await generate_gif(
        source,
        output,
        ffmpeg_path=Path(ffmpeg),
        timeout_seconds=30,
        size_limit_bytes=9_961_472,
        workdir=tmp_path / "work",
    )

    assert profile is GIF_PROFILES[0]
    assert output.is_file()
    assert output.stat().st_size <= 9_961_472

    probe = await run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name",
            "-of",
            "json",
            output,
        ],
        timeout_seconds=10,
    )
    streams = json.loads(probe.stdout)["streams"]
    assert [stream["codec_type"] for stream in streams] == ["video"]
    assert streams[0]["codec_name"] == "gif"


@pytest.mark.asyncio
async def test_generate_gif_falls_back_to_smaller_profiles_under_a_tight_limit(
    tmp_path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is required for the GIF export smoke test.")

    source = tmp_path / "source.mp4"
    await _make_motion_fixture(source, ffmpeg)
    output = tmp_path / "clip.gif"

    # Tight enough that the first (largest) profile can't fit, but the smallest
    # comfortably can — forces at least one fallback step.
    size_limit_bytes = 150_000
    profile = await generate_gif(
        source,
        output,
        ffmpeg_path=Path(ffmpeg),
        timeout_seconds=30,
        size_limit_bytes=size_limit_bytes,
        workdir=tmp_path / "work",
    )

    assert profile is not GIF_PROFILES[0]
    assert output.is_file()
    assert output.stat().st_size <= size_limit_bytes


@pytest.mark.asyncio
async def test_generate_gif_reports_a_structured_failure_when_no_profile_fits(
    tmp_path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is required for the GIF export smoke test.")

    source = tmp_path / "source.mp4"
    await _make_motion_fixture(source, ffmpeg)
    output = tmp_path / "clip.gif"

    with pytest.raises(GifSizeLimitExceededError) as excinfo:
        await generate_gif(
            source,
            output,
            ffmpeg_path=Path(ffmpeg),
            timeout_seconds=30,
            size_limit_bytes=1,
            workdir=tmp_path / "work",
        )

    error = excinfo.value
    assert error.job_error_code == "GIF_SIZE_LIMIT_EXCEEDED"
    assert error.job_retryable is False
    assert error.context["size_limit_bytes"] == 1
    assert len(error.context["attempts"]) == len(GIF_PROFILES)
    assert not output.exists()


def test_gif_path_is_deterministic_and_changes_with_any_cache_key_input(tmp_path) -> None:
    root = tmp_path / "gifs"
    base = gif_path(root, "clip-one", 1, 1_000, 2_000, 9_500_000)
    again = gif_path(root, "clip-one", 1, 1_000, 2_000, 9_500_000)
    assert base == again

    # Revision changes (a trim replace or metadata edit) invalidate the cache
    # implicitly — the new path simply doesn't match anything on disk.
    assert gif_path(root, "clip-one", 2, 1_000, 2_000, 9_500_000) != base
    # A different source fingerprint (re-rendered clip content) invalidates too.
    assert gif_path(root, "clip-one", 1, 1_001, 2_000, 9_500_000) != base
    assert gif_path(root, "clip-one", 1, 1_000, 2_001, 9_500_000) != base
    # A different requested size limit gets its own cache slot.
    assert gif_path(root, "clip-one", 1, 1_000, 2_000, 5_000_000) != base
    # A trim-editor export of a sub-range never collides with the whole-clip
    # export, or with a different range, even at the same revision/limit.
    ranged = gif_path(root, "clip-one", 1, 1_000, 2_000, 9_500_000, 0, 500)
    other_ranged = gif_path(root, "clip-one", 1, 1_000, 2_000, 9_500_000, 100, 600)
    assert ranged != base
    assert ranged != other_ranged


@pytest.mark.asyncio
async def test_generate_gif_range_exports_only_the_selected_span(tmp_path) -> None:
    """The trim dialog's "Export GIF" targets the selected in/out points on the
    already-persisted clip — it must produce a GIF covering just that span,
    not the whole clip, and must never touch/re-render the source file."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg and ffprobe are required for the GIF export smoke test.")

    source = tmp_path / "source.mp4"
    await _make_motion_fixture(source, ffmpeg)
    source_bytes_before = source.read_bytes()
    output = tmp_path / "clip.gif"

    await generate_gif(
        source,
        output,
        ffmpeg_path=Path(ffmpeg),
        timeout_seconds=30,
        size_limit_bytes=9_961_472,
        workdir=tmp_path / "work",
        start_ms=0,
        end_ms=500,
    )

    assert output.is_file()
    assert source.read_bytes() == source_bytes_before  # never re-rendered

    probe = await run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "json",
            output,
        ],
        timeout_seconds=10,
    )
    ranged_frames = int(json.loads(probe.stdout)["streams"][0]["nb_read_frames"])

    full_output = tmp_path / "full.gif"
    await generate_gif(
        source,
        full_output,
        ffmpeg_path=Path(ffmpeg),
        timeout_seconds=30,
        size_limit_bytes=9_961_472,
        workdir=tmp_path / "work-full",
    )
    full_probe = await run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "json",
            full_output,
        ],
        timeout_seconds=10,
    )
    full_frames = int(json.loads(full_probe.stdout)["streams"][0]["nb_read_frames"])

    assert ranged_frames < full_frames


def test_purge_gif_cache_removes_stale_entries_but_keeps_the_current_one(tmp_path) -> None:
    root = tmp_path / "gifs"
    root.mkdir(parents=True)
    current = gif_path(root, "clip-one", 2, 1_000, 2_000, 9_500_000)
    stale = gif_path(root, "clip-one", 1, 1_000, 2_000, 9_500_000)
    other_clip = gif_path(root, "clip-two", 1, 1_000, 2_000, 9_500_000)
    for path in (current, stale, other_clip):
        path.write_bytes(b"gif")

    assert set(stale_gif_paths(root, "clip-one")) == {current, stale}

    purge_gif_cache(root, "clip-one", keep=current)

    assert current.exists()
    assert not stale.exists()
    assert other_clip.exists()


async def _run_blocking(function, *args, **kwargs):
    return function(*args, **kwargs)


def _clip_payload(source: Path, *, revision: int = 1) -> dict:
    stat = source.stat()
    now = datetime(2026, 8, 31, 12, 0)
    return {
        "id": "clip-one",
        "title": "Pilot",
        "automatic_title": "Pilot",
        "library": "TV Shows",
        "media_type": "episode",
        "file_path": str(source),
        "duration_ms": 2_000,
        "revision": revision,
        "source_start_ms": 0,
        "source_end_ms": 2_000,
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
async def test_gif_export_job_generates_serves_from_cache_and_survives_a_replace(
    tmp_path,
) -> None:
    """End-to-end through the real job pipeline: enqueue, claim, execute, cache
    hit on a repeat request, and cache invalidation once the clip is replaced —
    the acceptance criteria this issue calls out by name."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is required for the GIF export job test.")

    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    clip_root.mkdir(parents=True)
    source = clip_root / "Pilot.mp4"
    await _make_motion_fixture(source, ffmpeg)
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    try:
        await insert_clip(engine, _clip_payload(source))
        settings = Settings(
            _env_file=None,
            private_data_dir=tmp_path / "private",
            work_dir=tmp_path / "work",
            clip_dir=clip_root,
            source_dirs=[tmp_path / "sources"],
            ffmpeg_path=Path(ffmpeg),
        )
        runner = JobRunner(
            engine,
            settings,
            run_blocking=_run_blocking,
            events=JobEventBroker(),
        )

        clip = await get_clip(engine, "clip-one", clip_root)
        source_stat = source.stat()
        plan = build_gif_job_plan(clip, source_stat, settings.gif_size_limit_bytes)
        queued = await enqueue_gif_job(engine, plan)
        claimed = await claim_next_job(engine, "run-token")
        assert claimed is not None and claimed.type == "gif_export"
        await runner._execute_claimed_job(claimed)

        snapshot = await get_job_snapshot(engine, queued.id)
        assert snapshot is not None and snapshot.state == "SUCCEEDED"
        gif_url = snapshot.result["gif_url"]
        expected_url = f"/api/clips/clip-one/gif?size_limit_bytes={settings.gif_size_limit_bytes}"
        assert gif_url == expected_url

        cached_path = gif_path(
            settings.resolved_gif_dir,
            "clip-one",
            1,
            source_stat.st_size,
            source_stat.st_mtime_ns,
            settings.gif_size_limit_bytes,
        )
        assert cached_path.is_file()

        # A repeat request for the same clip/revision/limit resolves to the exact
        # same cache file — no second job needed.
        second_plan = build_gif_job_plan(clip, source_stat, settings.gif_size_limit_bytes)
        assert second_plan.operation_hash == plan.operation_hash
        assert (
            gif_path(
                settings.resolved_gif_dir,
                "clip-one",
                1,
                source_stat.st_size,
                source_stat.st_mtime_ns,
                settings.gif_size_limit_bytes,
            )
            == cached_path
        )

        # A trim-editor export of just a sub-range must not evict the whole-clip
        # cache entry just generated above — both are valid at once.
        range_plan = build_gif_job_plan(
            clip, source_stat, settings.gif_size_limit_bytes, 0, 500
        )
        range_queued = await enqueue_gif_job(engine, range_plan)
        range_claimed = await claim_next_job(engine, "run-token-range")
        assert range_claimed is not None
        await runner._execute_claimed_job(range_claimed)
        range_snapshot = await get_job_snapshot(engine, range_queued.id)
        assert range_snapshot is not None and range_snapshot.state == "SUCCEEDED"
        assert cached_path.is_file()  # still present, not evicted by the range export
        range_cached_path = gif_path(
            settings.resolved_gif_dir,
            "clip-one",
            1,
            source_stat.st_size,
            source_stat.st_mtime_ns,
            settings.gif_size_limit_bytes,
            0,
            500,
        )
        assert range_cached_path.is_file()
        assert range_cached_path != cached_path

        # Simulate a trim replace bumping the clip's revision and re-encoding the
        # source in place — the old cache entry must be purged as stale.
        await _make_motion_fixture(source, ffmpeg)
        new_stat = source.stat()
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE clips SET revision = 2 WHERE id = 'clip-one'"),
            )
        purge_gif_cache(settings.resolved_gif_dir, "clip-one")
        assert not cached_path.exists()
        assert not range_cached_path.exists()

        new_gif_path = gif_path(
            settings.resolved_gif_dir,
            "clip-one",
            2,
            new_stat.st_size,
            new_stat.st_mtime_ns,
            settings.gif_size_limit_bytes,
        )
        assert new_gif_path != cached_path
        assert not new_gif_path.exists()
    finally:
        await engine.dispose()
