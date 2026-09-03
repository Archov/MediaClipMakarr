from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import mediaclipmakarr.api.settings as settings_api
import mediaclipmakarr.main as main_module
from mediaclipmakarr.application_settings import (
    ApplicationSettingsUpdate,
    get_effective_application_settings,
    save_persisted_application_settings,
)
from mediaclipmakarr.config import Settings
from mediaclipmakarr.database import create_database_engine, upgrade_database
from mediaclipmakarr.health import MediaToolInspection
from mediaclipmakarr.immich import ImmichConnectionResult
from mediaclipmakarr.plex import PlexConnectionResult


def api_settings(tmp_path, **overrides) -> Settings:
    source = tmp_path / "source"
    source.mkdir()
    return Settings(
        _env_file=None,
        private_data_dir=tmp_path / "private",
        work_dir=tmp_path / "work",
        clip_dir=tmp_path / "clips",
        source_dirs=[source],
        frontend_dist_dir=tmp_path / "missing-frontend",
        **overrides,
    )


def test_windows_timezone_separator_is_normalized() -> None:
    update = ApplicationSettingsUpdate(timezone=r"America\Chicago")

    assert update.timezone == "America/Chicago"


@pytest.mark.asyncio
async def test_timezone_catalog_distinguishes_default_from_saved_value(tmp_path) -> None:
    database_path = tmp_path / "application.db"
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    bootstrap = Settings(_env_file=None)
    try:
        defaults = await get_effective_application_settings(engine, bootstrap)
        await save_persisted_application_settings(
            engine, {"timezone": "America/Chicago"}
        )
        configured = await get_effective_application_settings(engine, bootstrap)
    finally:
        await engine.dispose()

    assert defaults.timezone == "UTC"
    assert defaults.timezone_configured is False
    assert "America/Chicago" in defaults.to_response().available_timezones
    assert configured.timezone == "America/Chicago"
    assert configured.timezone_configured is True


@pytest.mark.asyncio
async def test_default_x264_preset_is_veryfast(tmp_path) -> None:
    database_path = tmp_path / "application.db"
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    try:
        effective = await get_effective_application_settings(engine, Settings(_env_file=None))
    finally:
        await engine.dispose()

    assert effective.x264_preset == "veryfast"


@pytest.mark.asyncio
async def test_non_empty_environment_values_override_persisted_settings(tmp_path) -> None:
    database_path = tmp_path / "application.db"
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    local_prefix = str((tmp_path / "source").resolve())
    try:
        await save_persisted_application_settings(
            engine,
            {
                "plex_url": "http://database-plex:32400",
                "plex_token": "database-secret",
                "source_path_mappings": json.dumps(
                    [{"plex_prefix": "/database", "local_prefix": local_prefix}]
                ),
                "timezone": "Europe/London",
                "x264_preset": "slow",
                "immich_url": "http://database-immich:2283",
                "immich_api_key": "database-immich-secret",
                "immich_default_tag": "database-tag",
                "immich_auto_upload": "true",
                "immich_manage_remote": "true",
                "immich_tag_library": "false",
                "immich_tag_show": "false",
                "immich_tag_episode": "false",
            },
        )
        bootstrap = Settings(
            _env_file=None,
            plex_url="http://environment-plex:32400/",
            plex_token="environment-secret",
            source_path_mappings=json.dumps(
                [{"plex_prefix": r"d:\Media", "local_prefix": local_prefix}]
            ),
            timezone="America/Chicago",
            x264_preset="fast",
            immich_url="http://environment-immich:2283/",
            immich_api_key="environment-immich-secret",
            immich_default_tag="environment-tag",
            immich_auto_upload=False,
            immich_manage_remote=False,
            immich_tag_library=True,
            immich_tag_show=True,
            immich_tag_episode=True,
        )

        effective = await get_effective_application_settings(engine, bootstrap)
        empty_overrides = await get_effective_application_settings(
            engine,
            Settings(
                _env_file=None,
                plex_url=" ",
                plex_token="",
                source_path_mappings="",
                timezone=" ",
                x264_preset="",
                immich_url=" ",
                immich_api_key="",
                immich_default_tag=" ",
            ),
        )
    finally:
        await engine.dispose()

    assert effective.plex_url == "http://environment-plex:32400"
    assert effective.plex_token == "environment-secret"
    assert effective.source_path_mappings[0].plex_prefix == "D:/Media"
    assert effective.timezone == "America/Chicago"
    assert effective.x264_preset == "fast"
    assert effective.immich_url == "http://environment-immich:2283"
    assert effective.immich_api_key == "environment-immich-secret"
    assert effective.immich_default_tag == "environment-tag"
    assert effective.immich_auto_upload is False
    assert effective.immich_manage_remote is False
    assert effective.immich_tag_library is True
    assert effective.immich_tag_show is True
    assert effective.immich_tag_episode is True
    assert all(effective.environment_managed.values())
    assert empty_overrides.plex_url == "http://database-plex:32400"
    assert empty_overrides.plex_token == "database-secret"
    assert empty_overrides.x264_preset == "slow"
    assert empty_overrides.immich_url == "http://database-immich:2283"
    assert empty_overrides.immich_api_key == "database-immich-secret"
    assert empty_overrides.immich_default_tag == "database-tag"
    assert empty_overrides.immich_auto_upload is True
    assert empty_overrides.immich_manage_remote is True
    assert empty_overrides.immich_tag_library is False
    assert empty_overrides.immich_tag_show is False
    assert empty_overrides.immich_tag_episode is False
    assert not any(empty_overrides.environment_managed.values())


def test_settings_api_redacts_preserves_and_explicitly_clears_token(
    tmp_path, monkeypatch
) -> None:
    observed_connections: list[tuple[str, str | None]] = []

    async def healthy_media_tools(_settings):
        return MediaToolInspection(
            status="ok",
            message="Media tools are ready.",
            details={"identity_ok": True, "libx264": True, "aac": True},
        )

    async def capture_connection_token(plex_url, plex_token):
        observed_connections.append((plex_url, plex_token))
        return PlexConnectionResult(
            connected=True,
            code="PLEX_CONNECTED",
            message="Connected to Plex successfully.",
        )

    monkeypatch.setattr(main_module, "inspect_media_tools", healthy_media_tools)
    monkeypatch.setattr(settings_api, "test_plex_connection", capture_connection_token)
    settings = api_settings(tmp_path)
    secret = "must-not-appear-in-a-response"
    replacement_secret = "replacement-must-also-stay-secret"
    candidate_secret = "unsaved-candidate-secret"
    validation_secret = "validation-secret-must-not-leak"
    partial_candidate_secret = "partial-candidate-secret-must-not-leak"

    with TestClient(main_module.create_app(settings)) as client:
        created = client.put("/api/settings", json={"plex_token": secret})
        replaced = client.put("/api/settings", json={"plex_token": replacement_secret})
        preserved = client.put("/api/settings", json={"plex_token": ""})
        fetched = client.get("/api/settings")
        candidate_connection = client.post(
            "/api/settings/plex/test",
            json={
                "plex_url": "http://candidate-plex:32400",
                "plex_token": candidate_secret,
            },
        )
        url_only_update = client.put(
            "/api/settings",
            json={"plex_url": "http://untrusted.example:32400"},
        )
        saved_connection = client.post("/api/settings/plex/test")
        url_only_connection = client.post(
            "/api/settings/plex/test",
            json={"plex_url": "http://untrusted.example:32400"},
        )
        token_only_connection = client.post(
            "/api/settings/plex/test",
            json={"plex_token": partial_candidate_secret},
        )
        conflicting_update = client.put(
            "/api/settings",
            json={"plex_token": validation_secret, "clear_plex_token": True},
        )
        cleared = client.put("/api/settings", json={"clear_plex_token": True})

    assert created.status_code == 200
    assert replaced.status_code == 200
    assert created.json()["plex_token_configured"] is True
    assert replaced.json()["plex_token_configured"] is True
    assert preserved.json()["plex_token_configured"] is True
    assert fetched.json()["plex_token_configured"] is True
    assert candidate_connection.json()["connected"] is True
    assert url_only_update.status_code == 200
    assert url_only_update.json()["plex_url"] == "http://untrusted.example:32400"
    assert url_only_update.json()["plex_token_configured"] is True
    assert saved_connection.json()["connected"] is True
    assert url_only_connection.status_code == 422
    assert token_only_connection.status_code == 422
    assert conflicting_update.status_code == 422
    assert all("input" not in error for error in token_only_connection.json()["detail"])
    assert all("input" not in error for error in conflicting_update.json()["detail"])
    assert observed_connections == [
        ("http://candidate-plex:32400", candidate_secret),
        ("http://untrusted.example:32400", replacement_secret),
    ]
    assert cleared.json()["plex_token_configured"] is False
    serialized_responses = (
        created.text
        + replaced.text
        + preserved.text
        + fetched.text
        + token_only_connection.text
        + conflicting_update.text
        + cleared.text
    )
    assert secret not in serialized_responses
    assert replacement_secret not in serialized_responses
    assert candidate_secret not in serialized_responses
    assert validation_secret not in serialized_responses
    assert partial_candidate_secret not in serialized_responses
    assert "plex_token" not in fetched.json()


def test_settings_api_redacts_preserves_and_explicitly_clears_immich_api_key(
    tmp_path, monkeypatch
) -> None:
    observed_connections: list[tuple[str, str | None]] = []

    async def healthy_media_tools(_settings):
        return MediaToolInspection(
            status="ok",
            message="Media tools are ready.",
            details={"identity_ok": True, "libx264": True, "aac": True},
        )

    async def capture_connection_key(immich_url, immich_api_key):
        observed_connections.append((immich_url, immich_api_key))
        return ImmichConnectionResult(
            connected=True,
            code="IMMICH_CONNECTED",
            message="Connected to Immich successfully.",
        )

    monkeypatch.setattr(main_module, "inspect_media_tools", healthy_media_tools)
    monkeypatch.setattr(settings_api, "test_immich_connection", capture_connection_key)
    settings = api_settings(tmp_path)
    secret = "must-not-appear-in-a-response"
    replacement_secret = "replacement-must-also-stay-secret"
    candidate_secret = "unsaved-candidate-secret"
    validation_secret = "validation-secret-must-not-leak"
    partial_candidate_secret = "partial-candidate-secret-must-not-leak"

    with TestClient(main_module.create_app(settings)) as client:
        created = client.put("/api/settings", json={"immich_api_key": secret})
        replaced = client.put("/api/settings", json={"immich_api_key": replacement_secret})
        preserved = client.put("/api/settings", json={"immich_api_key": ""})
        fetched = client.get("/api/settings")
        candidate_connection = client.post(
            "/api/settings/immich/test",
            json={
                "immich_url": "http://candidate-immich:2283",
                "immich_api_key": candidate_secret,
            },
        )
        url_only_update = client.put(
            "/api/settings",
            json={"immich_url": "http://untrusted.example:2283"},
        )
        saved_connection = client.post("/api/settings/immich/test")
        url_only_connection = client.post(
            "/api/settings/immich/test",
            json={"immich_url": "http://untrusted.example:2283"},
        )
        key_only_connection = client.post(
            "/api/settings/immich/test",
            json={"immich_api_key": partial_candidate_secret},
        )
        conflicting_update = client.put(
            "/api/settings",
            json={"immich_api_key": validation_secret, "clear_immich_api_key": True},
        )
        cleared = client.put("/api/settings", json={"clear_immich_api_key": True})

    assert created.status_code == 200
    assert replaced.status_code == 200
    assert created.json()["immich_api_key_configured"] is True
    assert replaced.json()["immich_api_key_configured"] is True
    assert preserved.json()["immich_api_key_configured"] is True
    assert fetched.json()["immich_api_key_configured"] is True
    assert candidate_connection.json()["connected"] is True
    assert url_only_update.status_code == 200
    assert url_only_update.json()["immich_url"] == "http://untrusted.example:2283"
    assert url_only_update.json()["immich_api_key_configured"] is True
    assert saved_connection.json()["connected"] is True
    assert url_only_connection.status_code == 422
    assert key_only_connection.status_code == 422
    assert conflicting_update.status_code == 422
    assert observed_connections == [
        ("http://candidate-immich:2283", candidate_secret),
        ("http://untrusted.example:2283", replacement_secret),
    ]
    assert cleared.json()["immich_api_key_configured"] is False
    serialized_responses = (
        created.text
        + replaced.text
        + preserved.text
        + fetched.text
        + key_only_connection.text
        + conflicting_update.text
        + cleared.text
    )
    assert secret not in serialized_responses
    assert replacement_secret not in serialized_responses
    assert candidate_secret not in serialized_responses
    assert validation_secret not in serialized_responses
    assert partial_candidate_secret not in serialized_responses
    assert "immich_api_key" not in fetched.json()


def test_environment_managed_setting_cannot_be_updated_through_api(
    tmp_path, monkeypatch
) -> None:
    async def healthy_media_tools(_settings):
        return MediaToolInspection(
            status="ok",
            message="Media tools are ready.",
            details={"identity_ok": True, "libx264": True, "aac": True},
        )

    monkeypatch.setattr(main_module, "inspect_media_tools", healthy_media_tools)
    settings = api_settings(tmp_path, plex_url="http://environment-plex:32400")

    with TestClient(main_module.create_app(settings)) as client:
        fetched = client.get("/api/settings")
        response = client.put("/api/settings", json={"plex_url": "http://other:32400"})

    assert fetched.json()["environment_managed"]["plex_url"] is True
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ENVIRONMENT_MANAGED_SETTING"
    assert response.json()["detail"]["fields"] == ["plex_url"]


def test_environment_managed_immich_setting_cannot_be_updated_through_api(
    tmp_path, monkeypatch
) -> None:
    async def healthy_media_tools(_settings):
        return MediaToolInspection(
            status="ok",
            message="Media tools are ready.",
            details={"identity_ok": True, "libx264": True, "aac": True},
        )

    monkeypatch.setattr(main_module, "inspect_media_tools", healthy_media_tools)
    settings = api_settings(tmp_path, immich_url="http://environment-immich:2283")

    with TestClient(main_module.create_app(settings)) as client:
        fetched = client.get("/api/settings")
        response = client.put("/api/settings", json={"immich_url": "http://other:2283"})

    assert fetched.json()["environment_managed"]["immich_url"] is True
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ENVIRONMENT_MANAGED_SETTING"
    assert response.json()["detail"]["fields"] == ["immich_url"]


def test_effective_settings_are_cached_until_settings_update(tmp_path, monkeypatch) -> None:
    async def healthy_media_tools(_settings):
        return MediaToolInspection(
            status="ok",
            message="Media tools are ready.",
            details={"identity_ok": True, "libx264": True, "aac": True},
        )

    calls = 0
    original_loader = settings_api.get_effective_application_settings

    async def count_effective_settings_loads(engine, bootstrap):
        nonlocal calls
        calls += 1
        return await original_loader(engine, bootstrap)

    monkeypatch.setattr(main_module, "inspect_media_tools", healthy_media_tools)
    monkeypatch.setattr(
        main_module, "get_effective_application_settings", count_effective_settings_loads
    )
    monkeypatch.setattr(
        settings_api, "get_effective_application_settings", count_effective_settings_loads
    )
    settings = api_settings(tmp_path)

    with TestClient(main_module.create_app(settings)) as client:
        first_settings = client.get("/api/settings")
        first_sessions = client.get("/api/sessions")
        second_sessions = client.get("/api/sessions")
        updated = client.put("/api/settings", json={"timezone": "America/Chicago"})
        refreshed_settings = client.get("/api/settings")

    assert first_settings.status_code == 200
    assert first_sessions.status_code == 200
    assert second_sessions.status_code == 200
    assert updated.status_code == 200
    assert refreshed_settings.json()["timezone"] == "America/Chicago"
    assert calls == 2
