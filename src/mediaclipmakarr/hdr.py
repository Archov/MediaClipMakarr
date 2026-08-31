"""HDR classification and Dolby Vision render policy.

This module intentionally contains no FFmpeg command construction.  It turns
probe/Plex metadata into a durable classification that filter builders and the
UI can consume without repeating safety-sensitive Dolby Vision decisions.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

HdrRenderStrategy = Literal["sdr", "tone_map_hdr10", "tone_map_hlg", "reject_dolby_vision"]


class VideoColorMetadata(BaseModel):
    color_space: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_range: str | None = None


class PlexVideoMetadata(BaseModel):
    dynamic_range: str | None = None
    color_space: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_range: str | None = None
    dolby_vision_profile: int | None = None
    dolby_vision_bl_compatibility_id: int | None = None


class HdrCapabilities(BaseModel):
    hdr10: bool = False
    hlg: bool = False
    dolby_vision: bool = False
    dolby_vision_profile: int | None = None
    dolby_vision_base_layer_compatible: bool | None = None
    dolby_vision_bl_compatibility_id: int | None = None
    color: VideoColorMetadata = Field(default_factory=VideoColorMetadata)
    probe_context: dict[str, Any] = Field(default_factory=dict)


class ProbeVideoStream(Protocol):
    index: int
    codec_name: str | None
    color_space: str | None
    color_transfer: str | None
    color_primaries: str | None
    color_range: str | None
    side_data_list: list[dict[str, Any]]
    model_extra: dict[str, Any] | None


class AdvancedMediaError(RuntimeError):
    job_retryable = False

    def __init__(self, code: str, message: str, *, context: dict[str, Any]) -> None:
        super().__init__(message)
        self.job_error_code = code
        self.context = context


def classify_hdr(
    stream: ProbeVideoStream | None,
    plex: PlexVideoMetadata | None = None,
) -> HdrCapabilities:
    if stream is None:
        return HdrCapabilities()
    plex = plex or PlexVideoMetadata()
    color = VideoColorMetadata(
        color_space=stream.color_space or plex.color_space,
        color_transfer=stream.color_transfer or plex.color_transfer,
        color_primaries=stream.color_primaries or plex.color_primaries,
        color_range=stream.color_range or plex.color_range,
    )
    transfer = _normalized_transfer(color.color_transfer)
    dynamic_range = (plex.dynamic_range or "").casefold()
    side_data_types = [
        str(item.get("side_data_type"))
        for item in stream.side_data_list
        if item.get("side_data_type")
    ]
    has_static_hdr_metadata = any(
        name.casefold() in {"mastering display metadata", "content light level metadata"}
        for name in side_data_types
    )
    hdr10 = (
        transfer == "smpte2084"
        or (
            transfer != "arib-std-b67"
            and has_static_hdr_metadata
            and (color.color_primaries or "").casefold().replace("_", "") == "bt2020"
        )
        or ("hdr" in dynamic_range and "hlg" not in dynamic_range)
    )
    hlg = transfer == "arib-std-b67" or "hlg" in dynamic_range

    dovi = _dolby_vision_fields(stream)
    profile = _first_int(dovi.get("dv_profile"), plex.dolby_vision_profile)
    compatibility_id = _first_int(
        dovi.get("dv_bl_signal_compatibility_id"),
        dovi.get("bl_signal_compatibility_id"),
        plex.dolby_vision_bl_compatibility_id,
    )
    dolby_vision = (
        bool(dovi) or profile is not None or "dolby" in dynamic_range or "dovi" in dynamic_range
    )
    base_layer_compatible = (
        _dolby_base_layer_compatibility(
            profile=profile,
            compatibility_id=compatibility_id,
            bl_present=_optional_bool(dovi.get("bl_present_flag")),
            hdr10=hdr10,
            hlg=hlg,
        )
        if dolby_vision
        else None
    )

    context = {
        "stream_index": stream.index,
        "codec": stream.codec_name,
        "color_transfer": color.color_transfer,
        "color_primaries": color.color_primaries,
        "color_space": color.color_space,
        "color_range": color.color_range,
        "dolby_vision_profile": profile,
        "dolby_vision_bl_compatibility_id": compatibility_id,
        "dolby_vision_base_layer_compatible": base_layer_compatible,
        "side_data_types": side_data_types or None,
    }
    return HdrCapabilities(
        hdr10=hdr10,
        hlg=hlg,
        dolby_vision=dolby_vision,
        dolby_vision_profile=profile,
        dolby_vision_base_layer_compatible=base_layer_compatible,
        dolby_vision_bl_compatibility_id=compatibility_id,
        color=color,
        probe_context={key: value for key, value in context.items() if value is not None},
    )


def planned_hdr_strategy(hdr: HdrCapabilities) -> HdrRenderStrategy:
    if hdr.dolby_vision and hdr.dolby_vision_base_layer_compatible is not True:
        return "reject_dolby_vision"
    if hdr.hlg:
        return "tone_map_hlg"
    if hdr.hdr10:
        return "tone_map_hdr10"
    return "sdr"


def enforce_dolby_vision_policy(hdr: HdrCapabilities) -> None:
    if not hdr.dolby_vision:
        return
    if hdr.dolby_vision_profile == 5:
        raise AdvancedMediaError(
            "DOLBY_VISION_PROFILE_5_UNSUPPORTED",
            "Dolby Vision Profile 5 has no conventional HDR base layer and cannot be "
            "rendered safely.",
            context=hdr.probe_context,
        )
    if hdr.dolby_vision_base_layer_compatible is not True:
        raise AdvancedMediaError(
            "DOLBY_VISION_BASE_LAYER_INDETERMINATE",
            "Dolby Vision was detected, but a compatible HDR10 or HLG base layer could not be "
            "confirmed.",
            context=hdr.probe_context,
        )


def _dolby_vision_fields(stream: ProbeVideoStream) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for item in stream.side_data_list:
        side_data_type = str(item.get("side_data_type") or "").casefold()
        if side_data_type == "dovi configuration record" or any(
            str(key).casefold().startswith(("dv_", "dovi")) for key in item
        ):
            fields.update(item)
    for key, value in (stream.model_extra or {}).items():
        if str(key).casefold().startswith(("dv_", "dovi")):
            fields.setdefault(key, value)
    fields.pop("side_data_type", None)
    return fields


def _dolby_base_layer_compatibility(
    *,
    profile: int | None,
    compatibility_id: int | None,
    bl_present: bool | None,
    hdr10: bool,
    hlg: bool,
) -> bool | None:
    if profile == 5 or bl_present is False:
        return False
    if compatibility_id == 1:
        return hdr10
    if compatibility_id == 4:
        return hlg
    # Profile 7 is defined around a conventional HDR10 base layer.  A PQ tag
    # confirms that this is the base-layer representation exposed by ffprobe.
    if profile == 7 and hdr10:
        return True
    return None


def _normalized_transfer(value: str | None) -> str:
    normalized = (value or "").strip().casefold().replace("_", "-")
    aliases = {
        "pq": "smpte2084",
        "smpte-st-2084": "smpte2084",
        "st2084": "smpte2084",
        "hlg": "arib-std-b67",
        "arib-std-b67": "arib-std-b67",
    }
    return aliases.get(normalized, normalized)


def _first_int(*values: object) -> int | None:
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            return int(str(value))
        except ValueError:
            continue
    return None


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    return None
