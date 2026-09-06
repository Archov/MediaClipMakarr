from __future__ import annotations

import pytest

from mediaclipmakarr.hdr import AdvancedMediaError, HdrCapabilities, VideoColorMetadata
from mediaclipmakarr.video_filters import (
    build_video_base_filter,
    build_video_base_filter_gpu_hdr,
    build_video_frame_filter,
    output_color_args,
)


@pytest.mark.parametrize(
    ("strategy", "transfer"),
    [("tone_map_hdr10", "smpte2084"), ("tone_map_hlg", "arib-std-b67")],
)
def test_hdr_filter_is_high_precision_mobius_bt709_and_size_bounded(
    strategy: str, transfer: str
) -> None:
    hdr = HdrCapabilities(
        hdr10=strategy == "tone_map_hdr10",
        hlg=strategy == "tone_map_hlg",
        color=VideoColorMetadata(
            color_space="bt2020nc",
            color_transfer=transfer,
            color_primaries="bt2020",
            color_range="tv",
        ),
    )

    graph = build_video_base_filter(
        hdr,
        strategy,  # type: ignore[arg-type]
        max_width=1920,
        max_height=1080,
        max_fps=60,
        source_frame_rate=None,
    )

    assert f"color_trc={transfer}" in graph
    assert "color_primaries=bt2020" in graph
    assert "colorspace=bt2020nc:range=limited" in graph
    assert "tonemapx=tonemap=mobius:param=0.3:desat=0" in graph
    assert "transfer=bt709:matrix=bt709:primaries=bt709:range=tv" in graph
    assert "min(1920,iw)" in graph
    assert "min(1080,ih)" in graph
    assert graph.endswith("format=yuv420p")


@pytest.mark.parametrize(
    ("max_resolution", "width", "height"),
    [("4k", 3840, 2160), ("1080p", 1920, 1080), ("720p", 1280, 720), ("480p", 854, 480)],
)
def test_base_filter_resolution_cap_is_configurable(
    max_resolution: str, width: int, height: int
) -> None:
    hdr = HdrCapabilities()

    graph = build_video_base_filter(
        hdr,
        "sdr",
        max_width=width,
        max_height=height,
        max_fps=60,
        source_frame_rate=None,
    )

    assert f"min({width},iw)" in graph
    assert f"min({height},ih)" in graph


def test_base_filter_omits_fps_cap_when_source_already_under_it() -> None:
    hdr = HdrCapabilities()

    at_cap = build_video_base_filter(
        hdr, "sdr", max_width=1920, max_height=1080, max_fps=30, source_frame_rate=30.0
    )
    under_cap = build_video_base_filter(
        hdr, "sdr", max_width=1920, max_height=1080, max_fps=30, source_frame_rate=23.976
    )
    unknown = build_video_base_filter(
        hdr, "sdr", max_width=1920, max_height=1080, max_fps=30, source_frame_rate=None
    )

    assert "fps=" not in at_cap
    assert "fps=" not in under_cap
    assert "fps=" not in unknown


def test_base_filter_applies_fps_cap_only_when_source_exceeds_it() -> None:
    hdr = HdrCapabilities()

    graph = build_video_base_filter(
        hdr, "sdr", max_width=1920, max_height=1080, max_fps=30, source_frame_rate=60.0
    )

    assert "fps=30," in graph
    # fps must come before scale, not after — dropping frames is cheap and
    # shrinks the input to everything downstream; applied last it would only
    # discard work scale (or, for HDR, the much more expensive tonemapx)
    # already did on every source frame. A real bug this app shipped with,
    # caught because a 50fps->30fps cap wasn't actually speeding anything up.
    assert graph.index("fps=30") < graph.index("scale=")


def test_hdr_filter_applies_fps_cap_before_the_expensive_tonemap_step() -> None:
    hdr = HdrCapabilities(
        hdr10=True,
        color=VideoColorMetadata(
            color_space="bt2020nc",
            color_transfer="smpte2084",
            color_primaries="bt2020",
            color_range="tv",
        ),
    )

    graph = build_video_base_filter(
        hdr,
        "tone_map_hdr10",
        max_width=1920,
        max_height=1080,
        max_fps=30,
        source_frame_rate=60.0,
    )

    assert "fps=30," in graph
    assert graph.index("fps=30") < graph.index("tonemapx=")
    assert graph.index("fps=30") < graph.index("scale=")


def test_output_is_explicitly_tagged_limited_range_bt709() -> None:
    args = output_color_args()

    assert args == [
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-color_range",
        "tv",
    ]


def test_export_frame_tone_maps_without_reducing_source_resolution() -> None:
    hdr = HdrCapabilities(
        hdr10=True,
        color=VideoColorMetadata(
            color_space="bt2020nc",
            color_transfer="smpte2084",
            color_primaries="bt2020",
            color_range="tv",
        ),
    )

    export_graph = build_video_frame_filter(hdr, "tone_map_hdr10")
    thumbnail_graph = build_video_frame_filter(
        hdr,
        "tone_map_hdr10",
        max_width=480,
        max_height=270,
    )

    assert "tonemapx=tonemap=mobius" in export_graph
    assert "scale=w=" not in export_graph
    assert export_graph.endswith("format=rgb24")
    assert "min(480,iw)" in thumbnail_graph
    assert "min(270,ih)" in thumbnail_graph
    assert thumbnail_graph.endswith("format=rgb24")


@pytest.mark.parametrize("strategy", ["tone_map_hdr10", "tone_map_hlg"])
def test_gpu_hdr_filter_uses_libplacebo_mobius_bt709(strategy: str) -> None:
    hdr = HdrCapabilities(
        hdr10=strategy == "tone_map_hdr10",
        hlg=strategy == "tone_map_hlg",
        color=VideoColorMetadata(
            color_space="bt2020nc",
            color_transfer="smpte2084" if strategy == "tone_map_hdr10" else "arib-std-b67",
            color_primaries="bt2020",
            color_range="tv",
        ),
    )

    graph = build_video_base_filter_gpu_hdr(
        hdr,
        strategy,  # type: ignore[arg-type]
        max_width=1920,
        max_height=1080,
        max_fps=60,
        source_frame_rate=None,
    )

    assert graph.startswith("libplacebo=")
    assert "min(1920,iw)" in graph
    assert "min(1080,ih)" in graph
    assert "colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv" in graph
    assert "tonemapping=mobius:tonemapping_param=0.3" in graph
    assert "fps=" not in graph
    assert graph.endswith("format=yuv420p")


def test_gpu_hdr_filter_resolution_cap_is_configurable() -> None:
    hdr = HdrCapabilities(
        hdr10=True,
        color=VideoColorMetadata(
            color_space="bt2020nc",
            color_transfer="smpte2084",
            color_primaries="bt2020",
            color_range="tv",
        ),
    )

    graph = build_video_base_filter_gpu_hdr(
        hdr, "tone_map_hdr10", max_width=1280, max_height=720, max_fps=60, source_frame_rate=None
    )

    assert "min(1280,iw)" in graph
    assert "min(720,ih)" in graph


def test_gpu_hdr_filter_applies_fps_cap_only_when_source_exceeds_it() -> None:
    hdr = HdrCapabilities(
        hdr10=True,
        color=VideoColorMetadata(
            color_space="bt2020nc",
            color_transfer="smpte2084",
            color_primaries="bt2020",
            color_range="tv",
        ),
    )

    under_cap = build_video_base_filter_gpu_hdr(
        hdr, "tone_map_hdr10", max_width=1920, max_height=1080, max_fps=30, source_frame_rate=24.0
    )
    over_cap = build_video_base_filter_gpu_hdr(
        hdr, "tone_map_hdr10", max_width=1920, max_height=1080, max_fps=30, source_frame_rate=60.0
    )

    assert "fps=" not in under_cap
    assert ":fps=30" in over_cap


def test_gpu_hdr_filter_rejects_sdr_strategy() -> None:
    with pytest.raises(ValueError, match="only for HDR tonemap strategies"):
        build_video_base_filter_gpu_hdr(
            HdrCapabilities(),
            "sdr",  # type: ignore[arg-type]
            max_width=1920,
            max_height=1080,
            max_fps=60,
            source_frame_rate=None,
        )


def test_unsafe_dolby_vision_is_rejected_before_filter_graph_construction() -> None:
    hdr = HdrCapabilities(
        hdr10=True,
        dolby_vision=True,
        dolby_vision_profile=8,
        dolby_vision_base_layer_compatible=None,
        probe_context={"stream_index": 0, "dolby_vision_profile": 8},
    )

    with pytest.raises(AdvancedMediaError) as caught:
        build_video_base_filter(
            hdr,
            "reject_dolby_vision",  # type: ignore[arg-type]
            max_width=1920,
            max_height=1080,
            max_fps=60,
            source_frame_rate=None,
        )

    assert caught.value.job_error_code == "DOLBY_VISION_BASE_LAYER_INDETERMINATE"
