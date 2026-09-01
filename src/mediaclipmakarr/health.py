from __future__ import annotations

import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mediaclipmakarr.config import Settings
from mediaclipmakarr.subprocesses import CommandError, CommandResult, run_command

Status = Literal["ok", "degraded", "error"]


class ComponentHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status
    message: str
    details: dict[str, str | bool | int] = Field(default_factory=dict)


class DirectoryHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    mode: Literal["read-write", "read-only"]
    status: Status
    message: str


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status
    application: ComponentHealth
    database: ComponentHealth
    media_tools: ComponentHealth
    directories: list[DirectoryHealth]


@dataclass(frozen=True, slots=True)
class MediaToolInspection:
    status: Status
    message: str
    details: dict[str, str | bool | int]

    def as_component(self) -> ComponentHealth:
        return ComponentHealth(status=self.status, message=self.message, details=self.details)


CommandRunner = Callable[..., Awaitable[CommandResult]]


def _first_line(output: str) -> str:
    return output.splitlines()[0].strip() if output.splitlines() else "unknown"


def _listed(output: str, name: str, *, required_flag: str | None = None) -> bool:
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[0].startswith("--"):
            continue
        names = fields[1].split(",")
        if name in names and (required_flag is None or required_flag in fields[0]):
            return True
    return False


async def inspect_media_tools(
    settings: Settings,
    runner: CommandRunner = run_command,
) -> MediaToolInspection:
    timeout = settings.subprocess_timeout_seconds
    ffmpeg = os.fspath(settings.ffmpeg_path)
    ffprobe = os.fspath(settings.ffprobe_path)
    try:
        ffmpeg_version = await runner([ffmpeg, "-version"], timeout_seconds=timeout)
        ffprobe_version = await runner([ffprobe, "-version"], timeout_seconds=timeout)
        encoders = await runner(
            [ffmpeg, "-hide_banner", "-encoders"], timeout_seconds=timeout
        )
        filters = await runner(
            [ffmpeg, "-hide_banner", "-filters"], timeout_seconds=timeout
        )
        formats = await runner(
            [ffmpeg, "-hide_banner", "-formats"], timeout_seconds=timeout
        )
    except CommandError as error:
        return MediaToolInspection(
            status="error",
            message=(
                f"Media tools are unavailable: {error} Configure MCM_FFMPEG_PATH and "
                "MCM_FFPROBE_PATH to the pinned Jellyfin FFmpeg installation."
            ),
            details={"available": False},
        )

    ffmpeg_identity = _first_line(ffmpeg_version.stdout)
    ffprobe_identity = _first_line(ffprobe_version.stdout)
    identity_ok = all(
        settings.expected_ffmpeg_identity in identity
        for identity in (ffmpeg_identity, ffprobe_identity)
    ) and all("jellyfin" in identity.lower() for identity in (ffmpeg_identity, ffprobe_identity))
    capabilities = {
        "libx264": _listed(encoders.stdout, "libx264"),
        "aac": _listed(encoders.stdout, "aac"),
        "scale": _listed(filters.stdout, "scale"),
        "mp4_muxer": _listed(formats.stdout, "mp4", required_flag="E"),
    }
    missing = [name for name, present in capabilities.items() if not present]
    details: dict[str, str | bool | int] = {
        "available": True,
        "expected_identity": settings.expected_ffmpeg_identity,
        "ffmpeg_identity": ffmpeg_identity,
        "ffprobe_identity": ffprobe_identity,
        "identity_ok": identity_ok,
        **capabilities,
    }
    if not identity_ok:
        return MediaToolInspection(
            status="error",
            message=(
                "FFmpeg/ffprobe are not the expected Jellyfin build. Install the pinned "
                f"{settings.expected_ffmpeg_identity} build or correct the configured paths."
            ),
            details=details,
        )
    if missing:
        return MediaToolInspection(
            status="error",
            message=(
                "The Jellyfin FFmpeg build is missing required capabilities: "
                f"{', '.join(missing)}. Use the official pinned GPL portable build."
            ),
            details=details,
        )
    return MediaToolInspection(
        status="ok",
        message="The expected Jellyfin FFmpeg build and required capabilities are available.",
        details=details,
    )


def initialize_writable_directories(settings: Settings) -> dict[str, str | None]:
    """Create application-owned roots and return sanitized per-root errors."""

    results: dict[str, str | None] = {}
    for name, path in (
        ("private-data", settings.resolved_private_data_dir),
        ("work", settings.resolved_work_dir),
        ("clips", settings.resolved_clip_dir),
        ("thumbnails", settings.resolved_thumbnail_dir),
    ):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            results[name] = f"The configured {name} directory could not be created."
        else:
            results[name] = None
    return results


def inspect_directories(settings: Settings) -> list[DirectoryHealth]:
    reports: list[DirectoryHealth] = []
    for name, path in (
        ("private-data", settings.resolved_private_data_dir),
        ("work", settings.resolved_work_dir),
        ("clips", settings.resolved_clip_dir),
        ("thumbnails", settings.resolved_thumbnail_dir),
    ):
        reports.append(_inspect_writable_directory(name, path))

    if not settings.resolved_source_dirs:
        reports.append(
            DirectoryHealth(
                name="sources",
                mode="read-only",
                status="error",
                message="At least one source directory must be configured.",
            )
        )
    for index, path in enumerate(settings.resolved_source_dirs, start=1):
        if path.is_dir() and os.access(path, os.R_OK):
            reports.append(
                DirectoryHealth(
                    name=f"source-{index}",
                    mode="read-only",
                    status="ok",
                    message="The configured source directory is readable.",
                )
            )
        else:
            reports.append(
                DirectoryHealth(
                    name=f"source-{index}",
                    mode="read-only",
                    status="error",
                    message="The configured source directory is missing or unreadable.",
                )
            )
    return reports


def _inspect_writable_directory(name: str, path: Path) -> DirectoryHealth:
    if not path.is_dir():
        return DirectoryHealth(
            name=name,
            mode="read-write",
            status="error",
            message=f"The configured {name} directory is missing.",
        )
    try:
        with tempfile.NamedTemporaryFile(prefix=".mcm-health-", dir=path, delete=True):
            pass
    except OSError:
        return DirectoryHealth(
            name=name,
            mode="read-write",
            status="error",
            message=f"The configured {name} directory is not writable.",
        )
    return DirectoryHealth(
        name=name,
        mode="read-write",
        status="ok",
        message=f"The configured {name} directory is writable.",
    )
