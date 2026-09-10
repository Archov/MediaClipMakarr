from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from mediaclipmakarr.application_settings import EffectiveApplicationSettings
from mediaclipmakarr.config import Settings
from mediaclipmakarr.crop_probe import CropProbeError, detect_crop_for_session
from mediaclipmakarr.plex import PlexPartStream, PlexSession
from mediaclipmakarr.source_paths import SourcePathMapping
from mediaclipmakarr.subprocesses import CommandResult

_PILLARBOX_CROP_LINE = (
    "[Parsed_cropdetect_0 @ 0x1] x1:232 x2:1687 y1:0 y2:1079 w:1456 h:1072 "
    "x:232 y:4 pts:100 t:2.000000 limit:0.094118 crop=1456:1072:232:4\n"
)


def _probe_payload() -> str:
    return json.dumps(
        {
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "color_space": "bt709",
                    "color_transfer": "bt709",
                    "color_primaries": "bt709",
                    "color_range": "tv",
                },
                {"index": 1, "codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"duration": "3600.0"},
        }
    )


def _session(tmp_path) -> tuple[PlexSession, EffectiveApplicationSettings, Settings]:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "movie.mkv").write_bytes(b"fake media")
    session = PlexSession(
        session_identity="session-1",
        media_identity="media-1",
        title="A Movie",
        media_type="movie",
        plex_user=None,
        player=None,
        state="playing",
        position_ms=0,
        duration_ms=3_600_000,
        sampled_at=datetime(2026, 9, 7, tzinfo=UTC),
        plex_rating_key="501",
        plex_part_file="/plex/movie.mkv",
        selected_audio_streams=[PlexPartStream(stream_type=2, stream_index=1, selected=True)],
    )
    effective_settings = EffectiveApplicationSettings(
        plex_url=None,
        plex_token=None,
        source_path_mappings=[
            SourcePathMapping(plex_prefix="/plex", local_prefix=str(source_root))
        ],
        timezone="UTC",
        timezone_configured=True,
        x264_preset="veryfast",
        environment_managed={
            "plex_url": False,
            "plex_token": False,
            "source_path_mappings": False,
            "timezone": False,
            "x264_preset": False,
        },
    )
    settings = Settings(_env_file=None, source_dirs=[source_root], work_dir=tmp_path / "work")
    return session, effective_settings, settings


async def _run_blocking(function, *args):
    return function(*args)


@pytest.mark.asyncio
async def test_auto_detect_runs_two_probes_and_combines_matching_results(tmp_path) -> None:
    session, effective_settings, settings = _session(tmp_path)
    ffmpeg_calls: list[list[str]] = []

    async def runner(argv, **_kwargs):
        if "ffprobe" in str(argv[0]):
            return CommandResult(tuple(str(value) for value in argv), 0, _probe_payload(), "")
        ffmpeg_calls.append([str(value) for value in argv])
        return CommandResult(tuple(str(value) for value in argv), 0, "", _PILLARBOX_CROP_LINE)

    result = await detect_crop_for_session(
        session,
        10_000,
        40_000,
        None,
        effective_settings,
        settings,
        run_blocking=_run_blocking,
        runner=runner,
    )

    assert len(ffmpeg_calls) == 2
    assert result.source_width == 1920
    assert result.source_height == 1080
    assert result.crop_width == 1456
    # The 4px top/bottom margin (0.37% of 1080) is below the noise threshold
    # and is dropped — only the genuine 232px pillarboxing is applied.
    assert result.crop_height == 1080
    assert result.crop_x == 232
    assert result.crop_y == 0
    assert result.aspect_ratio_fraction == "4:3"
    assert result.aspect_ratio_decimal == "1.35:1"


@pytest.mark.asyncio
async def test_auto_detect_returns_none_crop_when_no_bars_are_found(tmp_path) -> None:
    session, effective_settings, settings = _session(tmp_path)

    async def runner(argv, **_kwargs):
        if "ffprobe" in str(argv[0]):
            return CommandResult(tuple(str(value) for value in argv), 0, _probe_payload(), "")
        return CommandResult(tuple(str(value) for value in argv), 0, "", "no crop info")

    result = await detect_crop_for_session(
        session,
        10_000,
        40_000,
        "auto",
        effective_settings,
        settings,
        run_blocking=_run_blocking,
        runner=runner,
    )

    assert result.crop_width is None
    assert result.aspect_ratio_fraction is None


@pytest.mark.asyncio
async def test_explicit_aspect_ratio_skips_probing_entirely(tmp_path) -> None:
    session, effective_settings, settings = _session(tmp_path)
    ffmpeg_calls: list[list[str]] = []

    async def runner(argv, **_kwargs):
        if "ffprobe" in str(argv[0]):
            return CommandResult(tuple(str(value) for value in argv), 0, _probe_payload(), "")
        ffmpeg_calls.append([str(value) for value in argv])
        raise AssertionError("Explicit aspect ratios must not probe with ffmpeg cropdetect.")

    result = await detect_crop_for_session(
        session,
        10_000,
        40_000,
        "4:3",
        effective_settings,
        settings,
        run_blocking=_run_blocking,
        runner=runner,
    )

    assert ffmpeg_calls == []
    assert result.crop_width == 1440
    assert result.crop_height == 1080
    assert result.aspect_ratio_fraction == "4:3"


@pytest.mark.asyncio
async def test_rejects_an_invalid_range(tmp_path) -> None:
    session, effective_settings, settings = _session(tmp_path)

    async def runner(argv, **_kwargs):
        return CommandResult(tuple(str(value) for value in argv), 0, _probe_payload(), "")

    with pytest.raises(CropProbeError):
        await detect_crop_for_session(
            session,
            40_000,
            10_000,
            None,
            effective_settings,
            settings,
            run_blocking=_run_blocking,
            runner=runner,
        )


@pytest.mark.asyncio
async def test_rejects_an_unparseable_aspect_ratio(tmp_path) -> None:
    session, effective_settings, settings = _session(tmp_path)

    async def runner(argv, **_kwargs):
        return CommandResult(tuple(str(value) for value in argv), 0, _probe_payload(), "")

    with pytest.raises(CropProbeError):
        await detect_crop_for_session(
            session,
            10_000,
            40_000,
            "not-a-ratio",
            effective_settings,
            settings,
            run_blocking=_run_blocking,
            runner=runner,
        )
