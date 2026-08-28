from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def _is_windows_plex_path(value: str) -> bool:
    return bool(_WINDOWS_DRIVE.match(value)) or value.startswith(("\\\\", "//")) or "\\" in value


def normalize_plex_path(value: str) -> tuple[str, bool]:
    """Normalize an absolute Plex path without resolving it on the local host."""

    raw = value.strip()
    if not raw:
        raise ValueError("Plex paths cannot be empty.")

    is_windows = _is_windows_plex_path(raw)
    pure_path = PureWindowsPath(raw) if is_windows else PurePosixPath(raw)
    if not pure_path.is_absolute():
        raise ValueError("Plex paths must be absolute Windows or POSIX paths.")
    if ".." in pure_path.parts:
        raise ValueError("Plex paths cannot contain parent-directory traversal.")

    normalized = pure_path.as_posix()
    if is_windows and pure_path.drive and not pure_path.drive.startswith("\\\\"):
        normalized = f"{pure_path.drive.upper()}{normalized[len(pure_path.drive):]}"
    return normalized.rstrip("/") or "/", is_windows


class SourcePathMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plex_prefix: str
    local_prefix: str

    @field_validator("plex_prefix", "local_prefix")
    @classmethod
    def require_non_empty_prefix(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Path mapping prefixes cannot be empty.")
        return value

    @model_validator(mode="after")
    def normalize_prefixes(self) -> SourcePathMapping:
        self.plex_prefix = normalize_plex_path(self.plex_prefix)[0]
        self.local_prefix = str(Path(self.local_prefix))
        if not Path(self.local_prefix).is_absolute():
            raise ValueError("Local/container prefixes must be absolute paths.")
        return self


def resolve_mapped_source_path(
    plex_path: str,
    mappings: list[SourcePathMapping],
    approved_source_roots: list[Path],
) -> Path:
    """Apply the first matching mapping and enforce canonical source-root containment."""

    normalized_path, path_is_windows = normalize_plex_path(plex_path)
    comparison_path = normalized_path.casefold() if path_is_windows else normalized_path

    for mapping in mappings:
        normalized_prefix, prefix_is_windows = normalize_plex_path(mapping.plex_prefix)
        if path_is_windows != prefix_is_windows:
            continue
        comparison_prefix = normalized_prefix.casefold() if path_is_windows else normalized_prefix
        if comparison_path != comparison_prefix and not comparison_path.startswith(
            f"{comparison_prefix}/"
        ):
            continue

        suffix = normalized_path[len(normalized_prefix) :].lstrip("/")
        mapping_root = Path(mapping.local_prefix).expanduser().resolve(strict=False)
        candidate = mapping_root.joinpath(*([part for part in suffix.split("/") if part])).resolve(
            strict=False
        )
        if not candidate.is_relative_to(mapping_root):
            raise ValueError("The mapped path escapes its configured local prefix.")

        approved_roots = [root.expanduser().resolve(strict=False) for root in approved_source_roots]
        if not any(candidate.is_relative_to(root) for root in approved_roots):
            raise ValueError("The mapped path is outside the approved read-only source roots.")
        return candidate

    raise ValueError("No configured source-path mapping matches the Plex path.")
