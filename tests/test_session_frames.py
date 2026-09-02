from __future__ import annotations

from pathlib import Path

from mediaclipmakarr.config import Settings
from mediaclipmakarr.hdr import HdrCapabilities, VideoColorMetadata
from mediaclipmakarr.session_frames import build_ffmpeg_frame_args


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
