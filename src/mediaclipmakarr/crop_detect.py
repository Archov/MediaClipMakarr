from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

_CROP_LINE = re.compile(r"crop=(?P<w>\d+):(?P<h>\d+):(?P<x>\d+):(?P<y>\d+)")

# (display label, decimal value) — the closest one within tolerance wins,
# so nearby entries (e.g. Academy 1.37:1 vs 4:3) don't depend on list order.
_COMMON_RATIOS: list[tuple[str, float]] = [
    ("1:1", 1.0),
    ("5:4", 5 / 4),
    ("4:3", 4 / 3),
    ("1.37:1", 11 / 8),  # Academy ratio
    ("3:2", 3 / 2),
    ("16:10", 16 / 10),
    ("16:9", 16 / 9),
    ("1.85:1", 1.85),
    ("1.90:1", 1.90),
    ("2:1", 2.0),
    ("21:9", 21 / 9),
    ("2.35:1", 2.35),
    ("2.39:1", 2.39),
]

# A detected margin narrower than this fraction of its dimension is within
# cropdetect's own threshold noise, not a reliable letterbox/pillarbox edge —
# trusting it risks trimming real picture content for an invisible gain.
_MIN_CROP_MARGIN_FRACTION = 0.02


@dataclass(frozen=True)
class CropMargins:
    left: int
    right: int
    top: int
    bottom: int


def build_cropdetect_probe_args(
    source_path: str, *, start_seconds: float, probe_seconds: float = 2.0
) -> list[str]:
    """ffmpeg args that run `cropdetect` over a short window starting at
    `start_seconds`. `cropdetect`'s own default `reset_count=0` accumulates the
    largest (safest, most conservative) detected video area across every frame
    in the window rather than resetting per-frame, so the last `crop=` line it
    prints already reflects the whole probe window — no need to average or
    pick among individual frames ourselves."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-ss",
        f"{max(start_seconds, 0.0):.3f}",
        "-i",
        source_path,
        "-t",
        f"{probe_seconds:.3f}",
        "-vf",
        "cropdetect",
        "-an",
        "-sn",
        "-f",
        "null",
        "-",
    ]


def parse_cropdetect_output(
    stderr: str, *, in_width: int, in_height: int
) -> CropMargins | None:
    """The margins implied by the last (most-accumulated) `crop=` line ffmpeg
    printed, or `None` if `cropdetect` never reported one (e.g. an all-black
    probe window)."""
    matches = list(_CROP_LINE.finditer(stderr))
    if not matches:
        return None
    last = matches[-1]
    width, height, x, y = (
        int(last.group("w")),
        int(last.group("h")),
        int(last.group("x")),
        int(last.group("y")),
    )
    return CropMargins(
        left=x,
        right=max(in_width - (x + width), 0),
        top=y,
        bottom=max(in_height - (y + height), 0),
    )


def combine_crop_margins(samples: list[CropMargins]) -> CropMargins | None:
    """Combine margins detected across multiple probe windows into one safe
    result: take the smallest margin seen at each edge across every sample,
    then mirror the smaller of each opposing pair onto both edges.

    Both steps exist for the same reason — an underdetected true edge (dark
    real content sitting next to the actual boundary) makes `cropdetect`
    over-crop that sample, reporting a *larger*-than-true margin, never a
    smaller one. So the smallest margin observed, at each edge and across
    mirrored edges, is always the conservative choice: it can under-crop
    (leave a sliver of the bar visible) but can never cut into real picture
    content.
    """
    if not samples:
        return None
    smallest_left = min(sample.left for sample in samples)
    smallest_right = min(sample.right for sample in samples)
    smallest_top = min(sample.top for sample in samples)
    smallest_bottom = min(sample.bottom for sample in samples)
    horizontal = min(smallest_left, smallest_right)
    vertical = min(smallest_top, smallest_bottom)
    return CropMargins(left=horizontal, right=horizontal, top=vertical, bottom=vertical)


def crop_filter_args(
    margins: CropMargins, *, in_width: int, in_height: int
) -> tuple[int, int, int, int] | None:
    """(w, h, x, y) for ffmpeg's `crop` filter, or `None` if the margins don't
    actually crop anything. Width/height are rounded down to even numbers
    (required for yuv420p output) by growing the margin, never shrinking it —
    so rounding can only be more conservative, never cut further into the
    frame than what was detected.

    A margin under `_MIN_CROP_MARGIN_FRACTION` of its dimension is dropped
    (treated as 0) rather than applied: at that size it's indistinguishable
    from `cropdetect`'s own threshold noise on real content, so applying it
    trims a sliver of real picture for no perceptible benefit.
    """
    left = margins.left if margins.left / in_width >= _MIN_CROP_MARGIN_FRACTION else 0
    right = margins.right if margins.right / in_width >= _MIN_CROP_MARGIN_FRACTION else 0
    top = margins.top if margins.top / in_height >= _MIN_CROP_MARGIN_FRACTION else 0
    bottom = margins.bottom if margins.bottom / in_height >= _MIN_CROP_MARGIN_FRACTION else 0
    width = in_width - left - right
    height = in_height - top - bottom
    if width <= 0 or height <= 0:
        return None
    even_width = width - (width % 2)
    even_height = height - (height % 2)
    if even_width <= 0 or even_height <= 0:
        return None
    if even_width == in_width and even_height == in_height:
        return None
    x = left + (width - even_width) // 2
    y = top + (height - even_height) // 2
    return even_width, even_height, x, y


def format_aspect_ratio(width: int, height: int) -> tuple[str, str]:
    """(fraction form, decimal form), e.g. `("4:3", "1.33:1")` — snapped to a
    common cinema/broadcast ratio when the pixel dimensions are close to one
    (crop dimensions rarely land on an exactly clean ratio once rounded to
    even numbers), else reduced to the nearest small-integer fraction."""
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive.")
    decimal = width / height
    decimal_form = f"{decimal:.2f}:1"
    candidates = [
        (label, value) for label, value in _COMMON_RATIOS if abs(decimal - value) / value < 0.02
    ]
    if candidates:
        label, _value = min(candidates, key=lambda candidate: abs(decimal - candidate[1]))
        return label, decimal_form
    fraction = Fraction(width, height).limit_denominator(20)
    return f"{fraction.numerator}:{fraction.denominator}", decimal_form


def parse_aspect_ratio(value: str) -> float:
    """Parse a user-entered ratio: `"4:3"`, `"1.85:1"`, or a bare decimal
    like `"1.85"`."""
    text = value.strip()
    if ":" in text:
        left, _, right = text.partition(":")
        width, height = float(left), float(right)
    else:
        width, height = float(text), 1.0
    if width <= 0 or height <= 0:
        raise ValueError("Aspect ratio must be positive.")
    return width / height


def crop_box_for_aspect_ratio(
    ratio: float, *, in_width: int, in_height: int
) -> tuple[int, int, int, int] | None:
    """A centered crop trimming `in_width`x`in_height` down to `ratio`
    (width/height), or `None` if the source already matches it closely
    enough that cropping would change nothing."""
    if ratio <= 0:
        raise ValueError("ratio must be positive.")
    if in_width <= 0 or in_height <= 0:
        raise ValueError("in_width and in_height must be positive.")
    source_ratio = in_width / in_height
    if abs(source_ratio - ratio) / ratio < 0.005:
        return None
    if source_ratio > ratio:
        # Source is relatively wider than the target — narrow the width
        # (pillarbox-style crop), height unchanged.
        target_width = _round_even(in_height * ratio)
        target_height = in_height
    else:
        # Source is relatively taller than the target — shorten the height
        # (letterbox-style crop), width unchanged.
        target_width = in_width
        target_height = _round_even(in_width / ratio)
    if target_width <= 0 or target_height <= 0:
        return None
    if target_width == in_width and target_height == in_height:
        return None
    x = (in_width - target_width) // 2
    y = (in_height - target_height) // 2
    return target_width, target_height, x, y


def _round_even(value: float) -> int:
    rounded = round(value)
    return rounded - (rounded % 2)
