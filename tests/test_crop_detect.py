from __future__ import annotations

import pytest

from mediaclipmakarr.crop_detect import (
    CropMargins,
    build_cropdetect_probe_args,
    combine_crop_margins,
    crop_box_for_aspect_ratio,
    crop_filter_args,
    format_aspect_ratio,
    parse_aspect_ratio,
    parse_cropdetect_output,
)


def test_probe_args_use_short_seek_and_null_output() -> None:
    args = build_cropdetect_probe_args(
        "/source/movie.mkv", start_seconds=5.0, probe_seconds=2.0
    )

    assert args[args.index("-ss") + 1] == "5.000"
    assert args[args.index("-i") + 1] == "/source/movie.mkv"
    assert args[args.index("-t") + 1] == "2.000"
    assert args[args.index("-vf") + 1] == "cropdetect"
    assert args[-2:] == ["-f", "null"] or args[-1] == "-"


def test_parse_takes_the_last_accumulated_crop_line() -> None:
    stderr = (
        "[Parsed_cropdetect_0 @ 0x1] x1:0 x2:1919 y1:100 y2:900 w:1920 h:800 x:0 y:100 "
        "crop=1920:800:0:100\n"
        "[Parsed_cropdetect_0 @ 0x1] x1:0 x2:1919 y1:132 y2:947 w:1920 h:816 x:0 y:132 "
        "crop=1920:816:0:132\n"
    )

    margins = parse_cropdetect_output(stderr, in_width=1920, in_height=1080)

    assert margins == CropMargins(left=0, right=0, top=132, bottom=1080 - (132 + 816))


def test_parse_returns_none_when_cropdetect_never_reported() -> None:
    assert parse_cropdetect_output("no crop info here", in_width=1920, in_height=1080) is None


def test_combine_takes_minimum_margin_across_samples() -> None:
    # A dark scene near the true edge in one sample over-detects (bigger
    # margin); the other sample sees the true, smaller margin. The smaller
    # one must win so real content is never cropped.
    dim_sample = CropMargins(left=0, right=0, top=140, bottom=140)
    true_sample = CropMargins(left=0, right=0, top=120, bottom=120)

    combined = combine_crop_margins([dim_sample, true_sample])

    assert combined == CropMargins(left=0, right=0, top=120, bottom=120)


def test_combine_mirrors_the_smaller_of_each_opposing_pair() -> None:
    # One edge under-detected relative to its mirror (e.g. a bright logo
    # near the bottom bar kept `cropdetect` from finding the full margin
    # there); the smaller, safer margin must be applied to both edges.
    asymmetric = CropMargins(left=10, right=12, top=130, bottom=60)

    combined = combine_crop_margins([asymmetric])

    assert combined == CropMargins(left=10, right=10, top=60, bottom=60)


def test_combine_returns_none_for_no_samples() -> None:
    assert combine_crop_margins([]) is None


def test_crop_filter_args_rounds_down_to_even_by_growing_the_margin() -> None:
    # top=101 leaves a height of 979 (odd) out of a 1080-tall frame.
    margins = CropMargins(left=0, right=0, top=101, bottom=0)

    result = crop_filter_args(margins, in_width=1920, in_height=1080)

    assert result is not None
    width, height, x, y = result
    assert width == 1920
    assert height == 978
    assert height % 2 == 0
    assert x == 0
    assert y >= 101  # grew inward from the detected margin, never outward past it


def test_crop_filter_args_returns_none_when_nothing_to_crop() -> None:
    assert crop_filter_args(
        CropMargins(left=0, right=0, top=0, bottom=0), in_width=1920, in_height=1080
    ) is None


def test_crop_filter_args_returns_none_when_margins_exceed_frame() -> None:
    assert crop_filter_args(
        CropMargins(left=1000, right=1000, top=0, bottom=0), in_width=1920, in_height=1080
    ) is None


def test_crop_filter_args_drops_a_margin_under_the_noise_threshold() -> None:
    # 4px of 1080 (0.37%) is within cropdetect's own threshold noise on real
    # content — trusting it would trim real picture for no visible benefit.
    # The 232px left/right pillarboxing (12%) is well above the threshold
    # and must still be applied.
    margins = CropMargins(left=232, right=232, top=4, bottom=4)

    result = crop_filter_args(margins, in_width=1920, in_height=1080)

    assert result == (1456, 1080, 232, 0)


def test_crop_filter_args_returns_none_when_only_the_margin_is_below_threshold() -> None:
    assert crop_filter_args(
        CropMargins(left=0, right=0, top=4, bottom=4), in_width=1920, in_height=1080
    ) is None


def test_format_aspect_ratio_snaps_pillarboxed_1080p_to_4_3() -> None:
    # A real pillarboxed 1080p source rarely crops to an exactly-4:3 pixel
    # box once rounded to even numbers — 1438x1080 is the closest even width.
    fraction, decimal = format_aspect_ratio(1438, 1080)

    assert fraction == "4:3"
    assert decimal == "1.33:1"


def test_format_aspect_ratio_matches_common_widescreen_ratios() -> None:
    assert format_aspect_ratio(1920, 1080) == ("16:9", "1.78:1")
    # 2560x1080 (2.370) is closer to 2.39:1 than to 21:9 (2.333) — closest
    # match wins, not just the first one listed within tolerance.
    assert format_aspect_ratio(2560, 1080) == ("2.39:1", "2.37:1")


def test_format_aspect_ratio_picks_the_closer_of_two_overlapping_common_ratios() -> None:
    # 1471x1080 (1.362) sits within 2% tolerance of both 4:3 (1.333) and the
    # Academy ratio 1.37:1 (1.375) — the closer one, 1.37:1, must win
    # regardless of which is listed first.
    assert format_aspect_ratio(1471, 1080) == ("1.37:1", "1.36:1")
    # 1438x1080 (1.331) is closer to 4:3 instead.
    assert format_aspect_ratio(1438, 1080) == ("4:3", "1.33:1")


def test_format_aspect_ratio_falls_back_to_reduced_fraction_for_unusual_ratios() -> None:
    fraction, decimal = format_aspect_ratio(1000, 337)
    assert decimal == "2.97:1"
    # No common-ratio match within tolerance — a small-denominator approximation.
    assert ":" in fraction


def test_parse_aspect_ratio_accepts_fraction_and_bare_decimal_forms() -> None:
    assert parse_aspect_ratio("4:3") == pytest.approx(4 / 3)
    assert parse_aspect_ratio("1.85:1") == pytest.approx(1.85)
    assert parse_aspect_ratio("1.85") == pytest.approx(1.85)


def test_parse_aspect_ratio_rejects_non_positive_values() -> None:
    with pytest.raises(ValueError):
        parse_aspect_ratio("0:1")
    with pytest.raises(ValueError):
        parse_aspect_ratio("-1")


def test_crop_box_for_aspect_ratio_narrows_width_for_a_taller_target() -> None:
    # 1920x1080 (16:9) cropped to 4:3 must narrow the width, centered, height unchanged.
    box = crop_box_for_aspect_ratio(4 / 3, in_width=1920, in_height=1080)

    assert box is not None
    width, height, x, y = box
    assert height == 1080
    assert width == 1440
    assert y == 0
    assert x == (1920 - 1440) // 2


def test_crop_box_for_aspect_ratio_shortens_height_for_a_wider_target() -> None:
    # 1920x1080 (16:9) cropped to 2.35:1 must shorten the height, centered, width unchanged.
    box = crop_box_for_aspect_ratio(2.35, in_width=1920, in_height=1080)

    assert box is not None
    width, height, x, y = box
    assert width == 1920
    assert height < 1080
    assert height % 2 == 0
    assert x == 0
    assert y == (1080 - height) // 2


def test_crop_box_for_aspect_ratio_returns_none_when_already_matching() -> None:
    assert crop_box_for_aspect_ratio(16 / 9, in_width=1920, in_height=1080) is None
