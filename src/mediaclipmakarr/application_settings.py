from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

from mediaclipmakarr.config import Settings
from mediaclipmakarr.source_paths import SourcePathMapping

X264_PRESETS = (
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
)
SETTING_FIELDS = (
    "plex_url",
    "plex_token",
    "source_path_mappings",
    "timezone",
    "x264_preset",
    "immich_url",
    "immich_api_key",
    "immich_default_tag",
    "immich_auto_upload",
    "immich_manage_remote",
    "immich_tag_library",
    "immich_tag_show",
    "immich_tag_episode",
)
_BOOLEAN_FIELDS = frozenset(
    {
        "immich_auto_upload",
        "immich_manage_remote",
        "immich_tag_library",
        "immich_tag_show",
        "immich_tag_episode",
    }
)
_DATABASE_KEYS = {field: f"setting.{field}" for field in SETTING_FIELDS}
AVAILABLE_TIMEZONES = sorted(available_timezones() | {"UTC"})


def _normalize_absolute_http_url(value: str, service_name: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{service_name} URL must be an absolute HTTP or HTTPS URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            f"{service_name} URL cannot contain credentials, a query string, or a fragment."
        )
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def normalize_plex_url(value: str) -> str:
    return _normalize_absolute_http_url(value, "Plex")


def normalize_immich_url(value: str) -> str:
    return _normalize_absolute_http_url(value, "Immich")


def _parse_bool(value: str) -> bool:
    return value.strip().casefold() in {"true", "1", "yes"}


def validate_timezone(value: str) -> str:
    value = value.strip().replace("\\", "/")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(
            "Timezone must be a valid IANA timezone, such as America/Chicago."
        ) from error
    return value


def validate_x264_preset(value: str) -> str:
    value = value.strip().lower()
    if value not in X264_PRESETS:
        raise ValueError(f"x264 preset must be one of: {', '.join(X264_PRESETS)}.")
    return value


class ApplicationSettingsResponse(BaseModel):
    plex_url: str
    plex_token_configured: bool
    source_path_mappings: list[SourcePathMapping]
    timezone: str
    timezone_configured: bool
    available_timezones: list[str]
    x264_preset: str
    immich_url: str
    immich_api_key_configured: bool
    immich_default_tag: str
    immich_auto_upload: bool
    immich_manage_remote: bool
    immich_tag_library: bool
    immich_tag_show: bool
    immich_tag_episode: bool
    environment_managed: dict[str, bool]


class ApplicationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plex_url: str | None = None
    plex_token: str | None = None
    clear_plex_token: bool = False
    source_path_mappings: list[SourcePathMapping] | None = None
    timezone: str | None = None
    x264_preset: str | None = None
    immich_url: str | None = None
    immich_api_key: str | None = None
    clear_immich_api_key: bool = False
    immich_default_tag: str | None = None
    immich_auto_upload: bool | None = None
    immich_manage_remote: bool | None = None
    immich_tag_library: bool | None = None
    immich_tag_show: bool | None = None
    immich_tag_episode: bool | None = None

    @field_validator("plex_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return None if value is None else normalize_plex_url(value)

    @field_validator("immich_url")
    @classmethod
    def validate_immich_url(cls, value: str | None) -> str | None:
        return None if value is None else normalize_immich_url(value)

    @field_validator("timezone")
    @classmethod
    def validate_timezone_name(cls, value: str | None) -> str | None:
        return None if value is None else validate_timezone(value)

    @field_validator("x264_preset")
    @classmethod
    def validate_preset(cls, value: str | None) -> str | None:
        return None if value is None else validate_x264_preset(value)

    @model_validator(mode="after")
    def validate_token_operation(self) -> ApplicationSettingsUpdate:
        if self.clear_plex_token and self.plex_token and self.plex_token.strip():
            raise ValueError("Set or clear the Plex token in one request, not both.")
        if self.clear_immich_api_key and self.immich_api_key and self.immich_api_key.strip():
            raise ValueError("Set or clear the Immich API key in one request, not both.")
        return self


@dataclass(frozen=True)
class EffectiveApplicationSettings:
    plex_url: str
    plex_token: str | None
    source_path_mappings: list[SourcePathMapping]
    timezone: str
    timezone_configured: bool
    x264_preset: str
    environment_managed: dict[str, bool]
    immich_url: str = ""
    immich_api_key: str | None = None
    immich_default_tag: str = ""
    immich_auto_upload: bool = False
    immich_manage_remote: bool = False
    immich_tag_library: bool = True
    immich_tag_show: bool = True
    immich_tag_episode: bool = True

    def to_response(self) -> ApplicationSettingsResponse:
        return ApplicationSettingsResponse(
            plex_url=self.plex_url,
            plex_token_configured=bool(self.plex_token),
            source_path_mappings=self.source_path_mappings,
            timezone=self.timezone,
            timezone_configured=self.timezone_configured,
            available_timezones=AVAILABLE_TIMEZONES,
            x264_preset=self.x264_preset,
            immich_url=self.immich_url,
            immich_api_key_configured=bool(self.immich_api_key),
            immich_default_tag=self.immich_default_tag,
            immich_auto_upload=self.immich_auto_upload,
            immich_manage_remote=self.immich_manage_remote,
            immich_tag_library=self.immich_tag_library,
            immich_tag_show=self.immich_tag_show,
            immich_tag_episode=self.immich_tag_episode,
            environment_managed=self.environment_managed,
        )


def _parse_mappings(value: str | None) -> list[SourcePathMapping] | None:
    if value is None or not value.strip():
        return None
    try:
        raw_mappings = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("MCM_SOURCE_PATH_MAPPINGS must be a JSON array.") from error
    if not isinstance(raw_mappings, list):
        raise ValueError("MCM_SOURCE_PATH_MAPPINGS must be a JSON array.")
    mappings = [SourcePathMapping.model_validate(mapping) for mapping in raw_mappings]
    return mappings or None


async def load_persisted_application_settings(engine: AsyncEngine) -> dict[str, str]:
    query = text("SELECT key, value FROM application_metadata WHERE key IN :keys").bindparams(
        bindparam("keys", expanding=True)
    )
    async with engine.connect() as connection:
        rows = await connection.execute(query, {"keys": list(_DATABASE_KEYS.values())})
        by_database_key = {str(row.key): str(row.value) for row in rows}
    return {
        field: by_database_key[database_key]
        for field, database_key in _DATABASE_KEYS.items()
        if database_key in by_database_key
    }


async def save_persisted_application_settings(
    engine: AsyncEngine, values: dict[str, str | None]
) -> None:
    unknown = set(values) - set(SETTING_FIELDS)
    if unknown:
        raise ValueError(f"Unknown application settings: {', '.join(sorted(unknown))}")

    upsert = text(
        "INSERT INTO application_metadata (key, value, updated_at) "
        "VALUES (:key, :value, CURRENT_TIMESTAMP) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP"
    )
    delete = text("DELETE FROM application_metadata WHERE key = :key")
    async with engine.begin() as connection:
        for field, value in values.items():
            database_key = _DATABASE_KEYS[field]
            if value is None:
                await connection.execute(delete, {"key": database_key})
            else:
                await connection.execute(upsert, {"key": database_key, "value": value})


async def get_effective_application_settings(
    engine: AsyncEngine, bootstrap: Settings
) -> EffectiveApplicationSettings:
    persisted = await load_persisted_application_settings(engine)

    persisted_mappings = _parse_mappings(persisted.get("source_path_mappings")) or []
    environment_mappings = _parse_mappings(bootstrap.source_path_mappings)
    environment_values: dict[str, object | None] = {
        "plex_url": bootstrap.plex_url.strip() if bootstrap.plex_url else None,
        "plex_token": bootstrap.plex_token.strip() if bootstrap.plex_token else None,
        "source_path_mappings": environment_mappings,
        "timezone": bootstrap.timezone.strip() if bootstrap.timezone else None,
        "x264_preset": bootstrap.x264_preset.strip() if bootstrap.x264_preset else None,
        "immich_url": bootstrap.immich_url.strip() if bootstrap.immich_url else None,
        "immich_api_key": bootstrap.immich_api_key.strip() if bootstrap.immich_api_key else None,
        "immich_default_tag": (
            bootstrap.immich_default_tag.strip() if bootstrap.immich_default_tag else None
        ),
        "immich_auto_upload": bootstrap.immich_auto_upload,
        "immich_manage_remote": bootstrap.immich_manage_remote,
        "immich_tag_library": bootstrap.immich_tag_library,
        "immich_tag_show": bootstrap.immich_tag_show,
        "immich_tag_episode": bootstrap.immich_tag_episode,
    }
    managed = {field: environment_values[field] is not None for field in SETTING_FIELDS}

    plex_url = str(environment_values["plex_url"] or persisted.get("plex_url", ""))
    if plex_url:
        try:
            plex_url = normalize_plex_url(plex_url)
        except ValueError:
            # Preserve an invalid environment value so the connection-test endpoint can
            # return PLEX_INVALID_URL instead of making the settings resource unavailable.
            plex_url = plex_url.strip()
    immich_url = str(environment_values["immich_url"] or persisted.get("immich_url", ""))
    if immich_url:
        try:
            immich_url = normalize_immich_url(immich_url)
        except ValueError:
            # Preserve an invalid environment value so the connection-test endpoint can
            # return IMMICH_INVALID_URL instead of making the settings resource unavailable.
            immich_url = immich_url.strip()
    timezone = validate_timezone(
        str(environment_values["timezone"] or persisted.get("timezone", "UTC"))
    )
    x264_preset = validate_x264_preset(
        str(environment_values["x264_preset"] or persisted.get("x264_preset", "veryfast"))
    )
    token_value = environment_values["plex_token"] or persisted.get("plex_token")
    immich_api_key_value = environment_values["immich_api_key"] or persisted.get("immich_api_key")

    def resolve_bool(field: str, default: bool) -> bool:
        environment_override = environment_values[field]
        if environment_override is not None:
            return bool(environment_override)
        if field in persisted:
            return _parse_bool(persisted[field])
        return default

    return EffectiveApplicationSettings(
        plex_url=plex_url,
        plex_token=str(token_value) if token_value else None,
        source_path_mappings=environment_mappings or persisted_mappings,
        timezone=timezone,
        timezone_configured=managed["timezone"] or "timezone" in persisted,
        x264_preset=x264_preset,
        immich_url=immich_url,
        immich_api_key=str(immich_api_key_value) if immich_api_key_value else None,
        immich_default_tag=str(
            environment_values["immich_default_tag"] or persisted.get("immich_default_tag", "")
        ),
        immich_auto_upload=resolve_bool("immich_auto_upload", False),
        immich_manage_remote=resolve_bool("immich_manage_remote", False),
        immich_tag_library=resolve_bool("immich_tag_library", True),
        immich_tag_show=resolve_bool("immich_tag_show", True),
        immich_tag_episode=resolve_bool("immich_tag_episode", True),
        environment_managed=managed,
    )


def managed_update_fields(
    update: ApplicationSettingsUpdate, managed: dict[str, bool]
) -> list[str]:
    attempted = set(update.model_fields_set) - {"clear_plex_token", "clear_immich_api_key"}
    if update.clear_plex_token:
        attempted.add("plex_token")
    if update.clear_immich_api_key:
        attempted.add("immich_api_key")
    return sorted(field for field in attempted if managed.get(field, False))


def serialize_update(update: ApplicationSettingsUpdate) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    for field in update.model_fields_set:
        if field in {"clear_plex_token", "plex_token", "clear_immich_api_key", "immich_api_key"}:
            continue
        value = getattr(update, field)
        if field == "source_path_mappings":
            if value is None:
                continue
            values[field] = json.dumps(
                [mapping.model_dump() for mapping in value], separators=(",", ":")
            )
        elif field in _BOOLEAN_FIELDS:
            if value is not None:
                values[field] = "true" if value else "false"
        elif value is not None:
            values[field] = str(value)

    if update.clear_plex_token:
        values["plex_token"] = None
    elif (
        "plex_token" in update.model_fields_set
        and update.plex_token
        and update.plex_token.strip()
    ):
        values["plex_token"] = update.plex_token.strip()

    if update.clear_immich_api_key:
        values["immich_api_key"] = None
    elif (
        "immich_api_key" in update.model_fields_set
        and update.immich_api_key
        and update.immich_api_key.strip()
    ):
        values["immich_api_key"] = update.immich_api_key.strip()
    return values
