from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mediaclipmakarr import __version__


class Settings(BaseSettings):
    """Environment-backed bootstrap settings.

    Source directories use a JSON array in ``MCM_SOURCE_DIRS`` so Windows drive
    letters are never confused with path-list separators.
    """

    model_config = SettingsConfigDict(
        env_prefix="MCM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "MediaClipMakarr"
    app_version: str = __version__
    private_data_dir: Path = Path("data/private")
    work_dir: Path = Path("data/work")
    clip_dir: Path = Path("data/clips")
    source_dirs: list[Path] = Field(default_factory=lambda: [Path("data/sources")])
    database_filename: str = "mediaclipmakarr.db"
    process_lock_filename: str = "mediaclipmakarr.lock"
    blocking_io_workers: int = Field(default=4, ge=1, le=16)
    # Tool inspection and source probes should fail quickly.  Preparation work runs
    # within a cancellable media job and may need to read substantially more input.
    subprocess_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    media_preparation_timeout_seconds: float = Field(default=300.0, gt=0, le=3_600)
    ffmpeg_path: Path = Path("ffmpeg")
    ffprobe_path: Path = Path("ffprobe")
    expected_ffmpeg_identity: str = "7.1.4-Jellyfin"
    frontend_dist_dir: Path = Path("frontend/dist")
    alembic_ini_path: Path = Path("alembic.ini")
    alembic_script_dir: Path = Path("alembic")
    dev_api_port: int = Field(default=8000, ge=1, le=65535)
    dev_web_port: int = Field(default=5173, ge=1, le=65535)
    plex_url: str | None = None
    plex_token: str | None = None
    source_path_mappings: str | None = None
    timezone: str | None = None
    x264_preset: str | None = None

    @field_validator(
        "plex_url", "plex_token", "source_path_mappings", "timezone", "x264_preset", mode="before"
    )
    @classmethod
    def ignore_empty_application_override(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("database_filename", "process_lock_filename")
    @classmethod
    def require_safe_filename(cls, value: str) -> str:
        if not value or value in {".", ".."} or any(
            separator in value for separator in ("/", "\\", ":")
        ):
            raise ValueError("Application data filenames must be plain filenames without paths.")
        return value

    def resolve_path(self, path: Path) -> Path:
        return path.expanduser().resolve(strict=False)

    @property
    def resolved_private_data_dir(self) -> Path:
        return self.resolve_path(self.private_data_dir)

    @property
    def resolved_work_dir(self) -> Path:
        return self.resolve_path(self.work_dir)

    @property
    def resolved_clip_dir(self) -> Path:
        return self.resolve_path(self.clip_dir)

    @property
    def resolved_source_dirs(self) -> list[Path]:
        return [self.resolve_path(path) for path in self.source_dirs]

    @property
    def database_path(self) -> Path:
        return self.resolved_private_data_dir / self.database_filename

    @property
    def process_lock_path(self) -> Path:
        return self.resolved_private_data_dir / self.process_lock_filename

    @property
    def resolved_frontend_dist_dir(self) -> Path:
        return self.resolve_path(self.frontend_dist_dir)

    @property
    def resolved_alembic_ini_path(self) -> Path:
        return self.resolve_path(self.alembic_ini_path)

    @property
    def resolved_alembic_script_dir(self) -> Path:
        return self.resolve_path(self.alembic_script_dir)


def validate_path_layout(settings: Settings) -> None:
    """Reject layouts where writable application data can overlap source media."""

    writable = {
        "private-data": settings.resolved_private_data_dir,
        "work": settings.resolved_work_dir,
        "clips": settings.resolved_clip_dir,
    }
    if len(set(writable.values())) != len(writable):
        raise ValueError("Private-data, work, and clip directories must be distinct.")

    for writable_name, writable_path in writable.items():
        for source_path in settings.resolved_source_dirs:
            if writable_path == source_path:
                raise ValueError(
                    f"The {writable_name} directory must not be the same as a source directory."
                )
            paths_overlap = writable_path.is_relative_to(
                source_path
            ) or source_path.is_relative_to(writable_path)
            if paths_overlap:
                raise ValueError(
                    f"The {writable_name} directory must not overlap a source directory."
                )
