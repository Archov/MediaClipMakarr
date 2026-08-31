from __future__ import annotations

import pytest

from mediaclipmakarr.hdr import AdvancedMediaError, HdrCapabilities, VideoColorMetadata
from mediaclipmakarr.video_filters import build_video_base_filter, output_color_args


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

    graph = build_video_base_filter(hdr, strategy)  # type: ignore[arg-type]

    assert f"color_trc={transfer}" in graph
    assert "color_primaries=bt2020" in graph
    assert "colorspace=bt2020nc:range=limited" in graph
    assert "zscale=transfer=linear" in graph
    assert "format=gbrpf32le" in graph
    assert "tonemap=tonemap=mobius" in graph
    assert "primaries=bt709:transfer=bt709:matrix=bt709:range=limited" in graph
    assert "min(1920,iw)" in graph
    assert "min(1080,ih)" in graph
    assert graph.endswith("format=yuv420p")


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


def test_unsafe_dolby_vision_is_rejected_before_filter_graph_construction() -> None:
    hdr = HdrCapabilities(
        hdr10=True,
        dolby_vision=True,
        dolby_vision_profile=8,
        dolby_vision_base_layer_compatible=None,
        probe_context={"stream_index": 0, "dolby_vision_profile": 8},
    )

    with pytest.raises(AdvancedMediaError) as caught:
        build_video_base_filter(hdr, "reject_dolby_vision")

    assert caught.value.job_error_code == "DOLBY_VISION_BASE_LAYER_INDETERMINATE"
