"""Composable FFmpeg video filter and output-color builders."""

from __future__ import annotations

from mediaclipmakarr.hdr import (
    AdvancedMediaError,
    HdrCapabilities,
    HdrRenderStrategy,
    enforce_dolby_vision_policy,
    planned_hdr_strategy,
)

_SIZE_FILTER = (
    "scale=w='min(1920,iw)':h='min(1080,ih)':"
    "force_original_aspect_ratio=decrease:force_divisible_by=2"
)


def build_video_base_filter(hdr: HdrCapabilities, strategy: HdrRenderStrategy) -> str:
    """Return the pre-subtitle filter chain for the immutable render strategy."""
    enforce_dolby_vision_policy(hdr)
    expected = planned_hdr_strategy(hdr)
    if strategy != expected:
        raise AdvancedMediaError(
            "HDR_RENDER_PLAN_MISMATCH",
            "The persisted HDR render strategy does not match the probed source metadata.",
            context={
                **hdr.probe_context,
                "planned_strategy": strategy,
                "expected_strategy": expected,
            },
        )
    if strategy == "sdr":
        return f"{_SIZE_FILTER},format=yuv420p"

    transfer = "smpte2084" if strategy == "tone_map_hdr10" else "arib-std-b67"
    primaries = _source_value(hdr.color.color_primaries, "bt2020")
    matrix = _source_value(hdr.color.color_space, "bt2020nc")
    source_range = _source_range(hdr.color.color_range)
    return (
        f"setparams=color_primaries={primaries}:color_trc={transfer}:"
        f"colorspace={matrix}:range={source_range},"
        "zscale=transfer=linear:npl=100,"
        "format=gbrpf32le,"
        "tonemap=tonemap=mobius:param=0.3:desat=0,"
        "zscale=primaries=bt709:transfer=bt709:matrix=bt709:range=limited,"
        f"{_SIZE_FILTER},format=yuv420p"
    )


def output_color_args() -> list[str]:
    """Explicitly tag every encoded clip as limited-range BT.709 SDR."""
    return [
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-color_range",
        "tv",
    ]


def _source_value(value: str | None, fallback: str) -> str:
    normalized = (value or "").strip().casefold().replace("_", "")
    aliases = {
        "bt2020nc": "bt2020nc",
        "bt2020ncl": "bt2020nc",
        "bt2020": "bt2020",
        "bt709": "bt709",
    }
    return aliases.get(normalized, fallback)


def _source_range(value: str | None) -> str:
    return "full" if (value or "").strip().casefold() in {"pc", "full", "jpeg"} else "limited"
