from __future__ import annotations

import pytest

from mediaclipmakarr.hdr import (
    AdvancedMediaError,
    PlexVideoMetadata,
    classify_hdr,
    enforce_dolby_vision_policy,
    planned_hdr_strategy,
)
from mediaclipmakarr.source_media import FFProbeStream


def video_stream(
    *,
    transfer: str | None,
    side_data: list[dict[str, object]] | None = None,
) -> FFProbeStream:
    return FFProbeStream(
        index=0,
        codec_type="video",
        codec_name="hevc",
        color_space="bt2020nc",
        color_transfer=transfer,
        color_primaries="bt2020",
        color_range="tv",
        side_data_list=side_data or [],
    )


@pytest.mark.parametrize(
    ("transfer", "expected_strategy"),
    [("smpte2084", "tone_map_hdr10"), ("arib-std-b67", "tone_map_hlg")],
)
def test_hdr10_and_hlg_are_classified_from_ffprobe(transfer: str, expected_strategy: str) -> None:
    hdr = classify_hdr(video_stream(transfer=transfer))

    assert planned_hdr_strategy(hdr) == expected_strategy
    assert hdr.color.color_primaries == "bt2020"


def test_plex_metadata_supplements_missing_probe_color_tags() -> None:
    hdr = classify_hdr(
        video_stream(transfer=None),
        PlexVideoMetadata(
            dynamic_range="HLG",
            color_transfer="arib-std-b67",
            color_primaries="bt2020",
            color_space="bt2020nc",
        ),
    )

    assert hdr.hlg is True
    assert planned_hdr_strategy(hdr) == "tone_map_hlg"


def test_hdr10_static_side_data_supplements_missing_transfer_tag() -> None:
    hdr = classify_hdr(
        video_stream(
            transfer=None,
            side_data=[{"side_data_type": "Mastering display metadata"}],
        )
    )

    assert hdr.hdr10 is True
    assert planned_hdr_strategy(hdr) == "tone_map_hdr10"


def test_profile_8_1_with_confirmed_hdr10_base_layer_is_supported() -> None:
    hdr = classify_hdr(
        video_stream(
            transfer="smpte2084",
            side_data=[
                {
                    "side_data_type": "DOVI configuration record",
                    "dv_profile": 8,
                    "bl_present_flag": 1,
                    "dv_bl_signal_compatibility_id": 1,
                }
            ],
        )
    )

    enforce_dolby_vision_policy(hdr)
    assert hdr.dolby_vision_base_layer_compatible is True
    assert planned_hdr_strategy(hdr) == "tone_map_hdr10"


def test_profile_5_is_rejected_with_stable_context() -> None:
    hdr = classify_hdr(
        video_stream(
            transfer=None,
            side_data=[
                {
                    "side_data_type": "DOVI configuration record",
                    "dv_profile": 5,
                    "bl_present_flag": 1,
                }
            ],
        )
    )

    with pytest.raises(AdvancedMediaError) as caught:
        enforce_dolby_vision_policy(hdr)

    assert caught.value.job_error_code == "DOLBY_VISION_PROFILE_5_UNSUPPORTED"
    assert caught.value.context["dolby_vision_profile"] == 5


def test_indeterminate_dolby_vision_cannot_enter_hdr_pipeline() -> None:
    hdr = classify_hdr(
        video_stream(
            transfer="smpte2084",
            side_data=[{"side_data_type": "DOVI configuration record", "dv_profile": 8}],
        )
    )

    assert planned_hdr_strategy(hdr) == "reject_dolby_vision"
    with pytest.raises(AdvancedMediaError) as caught:
        enforce_dolby_vision_policy(hdr)
    assert caught.value.job_error_code == "DOLBY_VISION_BASE_LAYER_INDETERMINATE"
