from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mediaclipmakarr.application_settings import EffectiveApplicationSettings
from mediaclipmakarr.config import Settings
from mediaclipmakarr.hdr import HdrCapabilities, VideoColorMetadata
from mediaclipmakarr.plex import PlexPartStream, PlexSession
from mediaclipmakarr.session_frames import build_ffmpeg_frame_args, render_session_frame
from mediaclipmakarr.source_paths import SourcePathMapping
from mediaclipmakarr.subprocesses import CommandResult


def hdr_capabilities() -> HdrCapabilities:
    return HdrCapabilities(
        hdr10=True,
        color=VideoColorMetadata(
            color_space="bt2020nc",
            color_transfer="smpte2084",
            color_primaries="bt2020",
            color_range="tv",
        ),
    )


def test_thumbnail_frame_is_bounded_tone_mapped_and_has_no_subtitles() -> None:
    args = build_ffmpeg_frame_args(
        "/source/movie.mkv",
        2,
        hdr_capabilities(),
        12_345,
        "thumbnail",
        Settings(_env_file=None),
        Path("thumbnail.png"),
    )

    video_filter = args[args.index("-vf") + 1]
    assert args[args.index("-ss") + 1] == "12.345"
    assert args[args.index("-map") + 1] == "0:2"
    assert "min(480,iw)" in video_filter
    assert "tonemap=tonemap=mobius" in video_filter
    assert "-sn" in args
    assert "subtitles=" not in video_filter
    assert "overlay=" not in video_filter


def test_export_frame_keeps_full_source_resolution_and_bt709_tags() -> None:
    output = Path("frame.png")
    args = build_ffmpeg_frame_args(
        "/source/movie.mkv",
        0,
        hdr_capabilities(),
        2_000,
        "export",
        Settings(_env_file=None),
        output,
    )

    video_filter = args[args.index("-vf") + 1]
    assert "scale=w=" not in video_filter
    assert "tonemap=tonemap=mobius" in video_filter
    assert args[args.index("-c:v") + 1] == "png"
    assert args[args.index("-update") + 1] == "1"
    assert video_filter.endswith("format=rgb24")
    assert args[-1] == str(Path(output))


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
                {
                    "index": 1,
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "tags": {"language": "eng"},
                },
            ],
            "format": {"duration": "60.0"},
        }
    )


async def _run_blocking(function, *args):
    return function(*args)


def _write_bytes(path: str, data: bytes) -> None:
    with open(path, "wb") as handle:
        handle.write(data)


@pytest.mark.asyncio
async def test_render_session_frame_filename_includes_source_stem(tmp_path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    media_path = source_root / "My Show S01E02.mkv"
    media_path.write_bytes(b"fake media")

    session = PlexSession(
        session_identity="session-1",
        media_identity="media-1",
        title="My Show",
        media_type="episode",
        plex_user=None,
        player=None,
        state="paused",
        position_ms=2_000,
        duration_ms=60_000,
        sampled_at=datetime(2026, 9, 2, tzinfo=UTC),
        plex_rating_key="501",
        plex_media_key="media-501",
        plex_part_id="part-501",
        plex_part_file="/plex/My Show S01E02.mkv",
        selected_audio_streams=[PlexPartStream(stream_type=2, stream_index=1, selected=True)],
    )
    effective_settings = EffectiveApplicationSettings(
        plex_url="http://plex.example:32400",
        plex_token="token",
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

    async def runner(argv, **_kwargs):
        if "ffprobe" in str(argv[0]):
            return CommandResult(tuple(str(value) for value in argv), 0, _probe_payload(), "")
        await _run_blocking(_write_bytes, str(argv[-1]), b"png-bytes")
        return CommandResult(tuple(str(value) for value in argv), 0, "", "")

    rendered = await render_session_frame(
        session,
        2_000,
        "export",
        effective_settings,
        settings,
        run_blocking=_run_blocking,
        runner=runner,
    )

    assert rendered.filename == "My Show S01E02-frame-2000ms.png"
