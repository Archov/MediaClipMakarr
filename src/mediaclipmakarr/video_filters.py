"""Composable FFmpeg video filter and output-color builders."""

from __future__ import annotations

from mediaclipmakarr.hdr import (
    AdvancedMediaError,
    HdrCapabilities,
    HdrRenderStrategy,
    enforce_dolby_vision_policy,
    planned_hdr_strategy,
)


def build_video_base_filter(
    hdr: HdrCapabilities,
    strategy: HdrRenderStrategy,
    *,
    max_width: int,
    max_height: int,
    max_fps: int,
    source_frame_rate: float | None,
) -> str:
    """Return the pre-subtitle filter chain for the immutable render strategy.

    `max_width`/`max_height`/`max_fps` are caps, never targets: the size
    filter's `min(cap,iw)` expression only ever shrinks a larger source, and
    the fps filter is omitted entirely (not just capped) unless the probed
    `source_frame_rate` is actually above `max_fps` — ffmpeg's `fps` filter
    duplicates frames to reach a rate the source doesn't have, which would
    be exactly the upscale/interpolation this is meant to avoid.
    """
    return _build_video_filter(
        hdr,
        strategy,
        size_filter=_bounded_size_filter(max_width, max_height),
        fps_filter=_fps_filter_segment(source_frame_rate, max_fps),
        output_pixel_format="yuv420p",
    )


def build_video_frame_filter(
    hdr: HdrCapabilities,
    strategy: HdrRenderStrategy,
    *,
    max_width: int | None = None,
    max_height: int | None = None,
) -> str:
    """Return a subtitle-free still-frame filter, optionally bounded for a thumbnail."""
    if (max_width is None) != (max_height is None):
        raise ValueError("Frame dimensions must either both be set or both be omitted.")
    size_filter = (
        _bounded_size_filter(max_width, max_height)
        if max_width is not None and max_height is not None
        else None
    )
    return _build_video_filter(
        hdr,
        strategy,
        size_filter=size_filter,
        fps_filter="",
        output_pixel_format="rgb24",
    )


def build_video_base_filter_gpu_hdr(
    hdr: HdrCapabilities,
    strategy: HdrRenderStrategy,
    *,
    max_width: int,
    max_height: int,
    max_fps: int,
    source_frame_rate: float | None,
) -> str:
    """GPU HDR->SDR path for a full clip render: `libplacebo` does scale,

    tonemap, and color conversion in one GPU filter. Verified ~9.6x faster
    than the CPU tonemapx chain on real HLG content on a GTX 1070 (41.5s ->
    4.3s for a 10s 4K/50fps clip, decode included). `tonemap_cuda` was tried
    first and produces badly underexposed output on HLG sources regardless
    of parameters — a known limitation, not a misconfiguration: its own
    author (the jellyfin-ffmpeg maintainer) describes it as a stripped-down
    `libplacebo` with lower color accuracy. Only for the HDR strategies —
    callers must route `strategy == "sdr"` to the plain CPU path instead,
    since libplacebo/Vulkan buys nothing there over the existing NVDEC path.

    `max_width`/`max_height`/`max_fps` are caps, never targets — same
    never-upscale contract as `build_video_base_filter` above: the `w`/`h`
    expressions only ever shrink, and the fps cap is omitted entirely unless
    the source is actually above it.

    The fps cap is deliberately a *separate* `fps=` filter appended after
    libplacebo, never libplacebo's own `fps` option. Verified on a real
    HLG broadcast file (decode itself clean, 0 errors): libplacebo's native
    `fps` option silently dropped every single frame — 0 encoded — while
    moving the identical cap to a plain trailing `fps=` filter produced the
    correct frame count with no other change. A real bug in libplacebo's
    own frame-rate handling on some real-world content, not a config issue.
    """
    _validate_strategy(hdr, strategy)
    if strategy == "sdr":
        raise ValueError("build_video_base_filter_gpu_hdr is only for HDR tonemap strategies.")
    fps_filter = _fps_filter_segment(source_frame_rate, max_fps)
    fps_suffix = f",{fps_filter.rstrip(',')}" if fps_filter else ""
    return (
        f"libplacebo=w='min({max_width},iw)':h='min({max_height},ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2:"
        "colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv:"
        f"tonemapping=mobius:tonemapping_param=0.3:format=yuv420p{fps_suffix}"
    )


def _validate_strategy(hdr: HdrCapabilities, strategy: HdrRenderStrategy) -> None:
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


def _build_video_filter(
    hdr: HdrCapabilities,
    strategy: HdrRenderStrategy,
    *,
    size_filter: str | None,
    fps_filter: str,
    output_pixel_format: str,
) -> str:
    _validate_strategy(hdr, strategy)
    if strategy == "sdr":
        return _finish_filter(size_filter, fps_filter, output_pixel_format)

    transfer = "smpte2084" if strategy == "tone_map_hdr10" else "arib-std-b67"
    primaries = _source_value(hdr.color.color_primaries, "bt2020")
    matrix = _source_value(hdr.color.color_space, "bt2020nc")
    source_range = _source_range(hdr.color.color_range)
    # tonemapx is ffmpeg's SIMD-optimized HDR->SDR tonemapper. Same mobius
    # curve as the old zscale-based chain, but ~2.6x faster on real HLG
    # content (measured locally) since it avoids the float-linear round
    # trip through gbrpf32le — verified visually equivalent output too.
    # fps_filter goes first, before tonemapx/scale: dropping frames is cheap
    # and shrinks the input to every expensive step downstream. Placed last,
    # it would only discard work tonemapx already paid for on every source
    # frame — a real bug this app shipped with, caught because a 50fps->30fps
    # cap wasn't actually speeding anything up.
    return (
        f"{fps_filter}setparams=color_primaries={primaries}:color_trc={transfer}:"
        f"colorspace={matrix}:range={source_range},"
        "tonemapx=tonemap=mobius:param=0.3:desat=0:"
        "transfer=bt709:matrix=bt709:primaries=bt709:range=tv,"
        f"{f'{size_filter},' if size_filter else ''}format={output_pixel_format}"
    )


def _bounded_size_filter(max_width: int, max_height: int) -> str:
    if max_width <= 0 or max_height <= 0:
        raise ValueError("Maximum frame dimensions must be positive.")
    return (
        f"scale=w='min({max_width},iw)':h='min({max_height},ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2"
    )


def _fps_filter_segment(source_frame_rate: float | None, max_fps: int) -> str:
    """A trailing `fps=<n>,` segment, or "" when no cap is needed.

    Omitted (not just capped) unless the source is actually above `max_fps`
    — ffmpeg's `fps` filter duplicates frames to reach a rate the source
    doesn't have, which would upscale/interpolate rather than only ever
    downscale/sample. An unprobeable frame rate is treated the same as
    "already under the cap": skipping the filter is always safe, applying
    it on a guess is not.
    """
    if source_frame_rate is None or source_frame_rate <= max_fps:
        return ""
    return f"fps={max_fps},"


def _finish_filter(size_filter: str | None, fps_filter: str, output_pixel_format: str) -> str:
    # fps_filter first — see the comment in _build_video_filter's HDR branch;
    # it also lets scale work on fewer frames when both apply.
    return f"{fps_filter}{f'{size_filter},' if size_filter else ''}format={output_pixel_format}"


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
