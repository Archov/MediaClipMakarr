from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import mediaclipmakarr.jobs.runner as runner_module
from mediaclipmakarr.clip_edits import (
    ClipEditError,
    ClipTrimSaveRequest,
    build_extended_trim_render_plan,
    build_trim_render_plan,
    is_extended_trim_request,
    recovered_audio_selection,
    recovered_subtitle_selection,
    source_fingerprint_matches,
    track_changed,
)
from mediaclipmakarr.clips import get_clip, insert_clip
from mediaclipmakarr.config import Settings
from mediaclipmakarr.database import create_database_engine, upgrade_database
from mediaclipmakarr.hdr import VideoColorMetadata
from mediaclipmakarr.jobs import (
    JobEventBroker,
    JobRunner,
    claim_next_job,
    enqueue_clip_create_job,
    get_job_snapshot,
    recover_finalizing_jobs,
)
from mediaclipmakarr.media_renderer import RenderedClipFile
from mediaclipmakarr.source_media import (
    NO_AUDIO_STREAM_INDEX,
    MediaStreamIdentity,
    ResolvedSourceMedia,
    SourceFingerprint,
    SubtitleSelection,
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


def test_trim_plan_does_not_reapply_the_parents_crop(tmp_path: Path) -> None:
    # A trim decodes from the already-rendered managed clip file
    # (`managed_source` below), which is already cropped and scaled — not
    # from the original pristine source. Reapplying the parent's crop box
    # (sized for the original, larger source) against that already-smaller
    # frame is invalid and made every trim of a cropped clip fail outright.
    path = tmp_path / "Example.mp4"
    path.write_bytes(b"managed clip")
    parent = parent_payload(path)
    parent.update({"crop_width": 1440, "crop_height": 1080, "crop_x": 240, "crop_y": 0})

    plan = trim_plan(parent, path)

    assert plan.crop_box is None
    assert (plan.crop_width, plan.crop_height, plan.crop_x, plan.crop_y) == (
        None,
        None,
        None,
        None,
    )


_UNSET = object()


def original_source(
    path: Path,
    *,
    duration_ms: int = 120_000,
    selected_audio_stream: object = _UNSET,
    selected_subtitle: SubtitleSelection | None = None,
) -> ResolvedSourceMedia:
    """A probe of the *original pristine source* (unlike `managed_source`
    above, which represents the already-rendered managed clip)."""
    stat = path.stat()
    if selected_audio_stream is _UNSET:
        selected_audio_stream = MediaStreamIdentity(stream_index=4, codec_type="audio", codec_name="aac")
    return ResolvedSourceMedia(
        plex_path=str(path),
        local_path=str(path),
        fingerprint=SourceFingerprint(
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
        ),
        duration_ms=duration_ms,
        video_streams=[
            VideoStreamIdentity(
                stream_index=0,
                codec_type="video",
                codec_name="h264",
                width=1920,
                height=1080,
                color=VideoColorMetadata(color_transfer="bt709"),
            )
        ],
        audio_streams=[selected_audio_stream] if selected_audio_stream is not None else [],
        subtitle_streams=[],
        selected_audio_stream=selected_audio_stream,
        selected_subtitle=selected_subtitle or SubtitleSelection(),
    )


def test_extended_trim_plan_reapplies_the_parents_crop(tmp_path: Path) -> None:
    # Unlike a normal trim, this decodes from the uncropped original source —
    # the crop box must be reapplied or the render comes out unframed.
    path = tmp_path / "Example.mkv"
    path.write_bytes(b"original source")
    parent = parent_payload(path)
    parent.update({"crop_width": 1440, "crop_height": 1080, "crop_x": 240, "crop_y": 0})

    plan = build_extended_trim_render_plan(
        parent,
        ClipTrimSaveRequest(start_ms=-5_000, end_ms=7_500, expected_revision=3, mode="new"),
        original_source(path),
        path.stat(),
        x264_preset="veryfast",
    )

    assert plan.crop_box == (1440, 1080, 240, 0)


def test_extended_trim_plan_uses_the_current_selection_not_the_granted_extend_amount(
    tmp_path: Path,
) -> None:
    """Regression: the absolute source range must come from the CURRENT
    selection (start_ms/end_ms), not whatever amount the extend buttons ever
    granted — extending left 5s then nudging Start back in 2s must save at
    source_start_ms - 3000, not - 5000."""
    path = tmp_path / "Example.mkv"
    path.write_bytes(b"original source")
    parent = parent_payload(path)  # source_start_ms=50_000, source_end_ms=60_000

    plan = build_extended_trim_render_plan(
        parent,
        ClipTrimSaveRequest(start_ms=-3_000, end_ms=7_500, expected_revision=3, mode="new"),
        original_source(path),
        path.stat(),
        x264_preset="veryfast",
    )

    assert (plan.source_start_ms, plan.source_end_ms) == (47_000, 57_500)
    assert (plan.provenance_start_ms, plan.provenance_end_ms) == (47_000, 57_500)


def test_extended_trim_plan_allows_a_selection_entirely_in_the_extended_region(
    tmp_path: Path,
) -> None:
    """Regression: `ClipTrimSaveRequest.end_ms` used to require `> 0`, which
    rejected a selection that sits wholly before the original clip's start
    (both start_ms and end_ms negative) once extend has granted that room —
    exactly the "trim entirely within the new extended portion" workflow."""
    path = tmp_path / "Example.mkv"
    path.write_bytes(b"original source")
    parent = parent_payload(path)  # source_start_ms=50_000, source_end_ms=60_000

    plan = build_extended_trim_render_plan(
        parent,
        ClipTrimSaveRequest(start_ms=-8_000, end_ms=-2_000, expected_revision=3, mode="new"),
        original_source(path),
        path.stat(),
        x264_preset="veryfast",
    )

    assert (plan.source_start_ms, plan.source_end_ms) == (42_000, 48_000)


def test_extended_trim_plan_rejects_a_range_before_the_source_starts(tmp_path: Path) -> None:
    path = tmp_path / "Example.mkv"
    path.write_bytes(b"original source")
    parent = parent_payload(path)  # source_start_ms=50_000

    with pytest.raises(ClipEditError) as error:
        build_extended_trim_render_plan(
            parent,
            ClipTrimSaveRequest(start_ms=-60_000, end_ms=7_500, expected_revision=3, mode="new"),
            original_source(path),
            path.stat(),
            x264_preset="veryfast",
        )

    assert error.value.job_error_code == "CLIP_RANGE_BEFORE_SOURCE_START"


def test_extended_trim_plan_rejects_a_range_past_the_source_end(tmp_path: Path) -> None:
    path = tmp_path / "Example.mkv"
    path.write_bytes(b"original source")
    parent = parent_payload(path)  # source_start_ms=50_000

    with pytest.raises(ClipEditError) as error:
        build_extended_trim_render_plan(
            parent,
            ClipTrimSaveRequest(start_ms=0, end_ms=20_000, expected_revision=3, mode="new"),
            original_source(path, duration_ms=65_000),  # absolute end would be 70_000
            path.stat(),
            x264_preset="veryfast",
        )

    assert error.value.job_error_code == "CLIP_RANGE_AFTER_SOURCE_END"


def test_extended_trim_plan_records_the_no_audio_sentinel_when_disabled(tmp_path: Path) -> None:
    path = tmp_path / "Example.mkv"
    path.write_bytes(b"original source")
    parent = parent_payload(path)

    plan = build_extended_trim_render_plan(
        parent,
        ClipTrimSaveRequest(
            start_ms=0, end_ms=5_000, expected_revision=3, mode="new", audio_disabled=True
        ),
        original_source(path, selected_audio_stream=None),
        path.stat(),
        x264_preset="veryfast",
    )

    assert plan.selected_audio_stream is None
    assert plan.provenance_audio_stream_index == NO_AUDIO_STREAM_INDEX


def test_recovered_audio_selection_maps_the_no_audio_sentinel() -> None:
    assert recovered_audio_selection({"selected_audio_stream_index": NO_AUDIO_STREAM_INDEX}) == (
        None,
        True,
    )
    assert recovered_audio_selection({"selected_audio_stream_index": 4}) == (4, False)


def test_recovered_subtitle_selection_preserves_an_external_selection() -> None:
    embedded = {
        "selectedSubtitle": {
            "enabled": True,
            "stream": None,
            "strategy": "external_text",
            "external_url": "http://plex.example:32400/library/streams/501.srt",
        }
    }

    recovered = recovered_subtitle_selection(embedded)

    assert recovered.enabled is True
    assert recovered.strategy == "external_text"
    assert recovered.external_url == "http://plex.example:32400/library/streams/501.srt"


def test_recovered_subtitle_selection_defaults_to_off_without_embedded_metadata() -> None:
    assert recovered_subtitle_selection(None) == SubtitleSelection()
    assert recovered_subtitle_selection({}) == SubtitleSelection()


def test_track_changed_treats_explicit_audio_disabled_as_a_change() -> None:
    unchanged = ClipTrimSaveRequest(start_ms=0, end_ms=5_000, expected_revision=1, mode="new")
    assert track_changed(unchanged) is False

    # audio_disabled defaults False, so "changed" can't be inferred from mere
    # field presence — an explicit False-that-means-disabled must still count.
    explicitly_disabled = ClipTrimSaveRequest(
        start_ms=0, end_ms=5_000, expected_revision=1, mode="new", audio_disabled=True
    )
    assert track_changed(explicitly_disabled) is True


def test_is_extended_trim_request_covers_out_of_bounds_ranges_and_track_changes() -> None:
    duration_ms = 10_000
    in_bounds = ClipTrimSaveRequest(start_ms=0, end_ms=5_000, expected_revision=1, mode="new")
    assert is_extended_trim_request(in_bounds, duration_ms) is False

    extended_before = ClipTrimSaveRequest(start_ms=-1, end_ms=5_000, expected_revision=1, mode="new")
    assert is_extended_trim_request(extended_before, duration_ms) is True

    extended_after = ClipTrimSaveRequest(
        start_ms=0, end_ms=duration_ms + 1, expected_revision=1, mode="new"
    )
    assert is_extended_trim_request(extended_after, duration_ms) is True

    track_only = ClipTrimSaveRequest(
        start_ms=0, end_ms=5_000, expected_revision=1, mode="new", subtitle_stream_index=2
    )
    assert is_extended_trim_request(track_only, duration_ms) is True


def test_source_fingerprint_matches_compares_size_and_modified_time(tmp_path: Path) -> None:
    path = tmp_path / "Example.mkv"
    path.write_bytes(b"original source")
    stat = path.stat()
    clip = {
        "source_size_bytes": stat.st_size,
        "source_modified_at": datetime.fromtimestamp(stat.st_mtime, UTC),
    }

    assert source_fingerprint_matches(clip, stat) is True
    assert source_fingerprint_matches(dict(clip, source_size_bytes=stat.st_size + 1), stat) is False


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


@pytest.mark.asyncio
async def test_replace_failure_after_swap_leaves_job_finalizing_for_recovery(
    monkeypatch, tmp_path: Path
) -> None:
    """`install_metadata_revision`'s file swap is atomic and irreversible — a
    failure after it (e.g. a database write dying) must never be reported as a
    plain FAILED job. Doing so would strand the database's stale revision
    permanently out of sync with the file, since `recover_finalizing_jobs`
    only reconciles jobs still in FINALIZING, and only runs at startup."""
    database = tmp_path / "application.db"
    clip_dir = tmp_path / "clips"
    source = clip_dir / "Movies" / "Example.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original bytes")
    upgrade_database(database)
    engine = create_database_engine(database)
    parent = parent_payload(source)
    await insert_clip(engine, parent)
    plan = trim_plan(parent, source, "replace")
    queued = await enqueue_clip_create_job(engine, plan)

    async def renderer(plan, settings, *, progress):
        await progress(1, "rendered")
        output = settings.resolved_work_dir / "jobs" / plan.job_id / "rendered.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        # Recovery identifies an already-swapped file by scanning for this
        # embedded envelope (see embedded_revision_matches) — a real render
        # embeds one, so the fake output here must too, or recovery can never
        # confirm the swap already happened and tell it apart from one that
        # needs to be redone from a (by-then-consumed) temp file.
        envelope = json.dumps(
            {
                "application": "MediaClipMakarr",
                "schemaVersion": 4,
                "clipId": plan.clip_id,
                "revision": plan.revision,
                "renderPlanHash": plan.render_plan_hash,
            }
        )
        output.write_bytes(f"validated replacement MediaClipMakarr {envelope}".encode())
        return RenderedClipFile(path=output, duration_ms=6_250)

    async def accept_output(*_args, **_kwargs):
        return None

    async def failing_commit(*_args, **_kwargs):
        raise RuntimeError("database is unavailable")

    monkeypatch.setattr(runner_module, "validate_trim_rendered_output", accept_output)
    monkeypatch.setattr(runner_module, "commit_clip_replacement", failing_commit)
    settings = Settings(
        _env_file=None,
        clip_dir=clip_dir,
        work_dir=tmp_path / "work",
        thumbnail_dir=tmp_path / "thumbs",
    )
    runner = JobRunner(
        engine,
        settings,
        run_blocking=run_blocking,
        events=JobEventBroker(),
        renderer=renderer,
    )
    claimed = await claim_next_job(engine, "run-token")
    assert claimed is not None
    try:
        await runner._execute_and_finalize(claimed)
        snapshot = await get_job_snapshot(engine, queued.id)
        stored = await get_clip(engine, "clip-parent", clip_dir)
        workdir = settings.resolved_work_dir / "jobs" / queued.id
    finally:
        await engine.dispose()

    assert snapshot is not None and snapshot.state == "FINALIZING"
    assert stored is not None and stored["revision"] == 3
    assert source.read_bytes().startswith(b"validated replacement")
    assert workdir.exists()

    # Recovery reconciles the database with the already-swapped file on the
    # next startup, rather than this being stuck forever.
    recovery_engine = create_database_engine(database)
    try:
        recovered = await recover_finalizing_jobs(
            recovery_engine,
            run_blocking,
            clip_root=clip_dir,
            work_root=settings.resolved_work_dir,
        )
        final_snapshot = await get_job_snapshot(recovery_engine, queued.id)
        final_clip = await get_clip(recovery_engine, "clip-parent", clip_dir)
    finally:
        await recovery_engine.dispose()

    assert recovered == [queued.id]
    assert final_snapshot is not None and final_snapshot.state == "SUCCEEDED"
    assert final_clip is not None and final_clip["revision"] == 4
