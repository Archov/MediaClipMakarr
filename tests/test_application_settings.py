from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import mediaclipmakarr.main as main_module
from mediaclipmakarr.application_settings import (
    get_effective_application_settings,
    save_persisted_application_settings,
)
from mediaclipmakarr.config import Settings
from mediaclipmakarr.database import create_database_engine, upgrade_database
from mediaclipmakarr.health import MediaToolInspection


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
            ),
        )
    finally:
        await engine.dispose()

    assert effective.plex_url == "http://environment-plex:32400"
    assert effective.plex_token == "environment-secret"
    assert effective.source_path_mappings[0].plex_prefix == "D:/Media"
    assert effective.timezone == "America/Chicago"
    assert effective.x264_preset == "fast"
    assert all(effective.environment_managed.values())
    assert empty_overrides.plex_url == "http://database-plex:32400"
    assert empty_overrides.plex_token == "database-secret"
    assert not any(empty_overrides.environment_managed.values())


def test_settings_api_redacts_preserves_and_explicitly_clears_token(
    tmp_path, monkeypatch
) -> None:
    async def healthy_media_tools(_settings):
        return MediaToolInspection(
            status="ok",
            message="Media tools are ready.",
            details={"identity_ok": True, "libx264": True, "aac": True},
        )

    monkeypatch.setattr(main_module, "inspect_media_tools", healthy_media_tools)
    settings = api_settings(tmp_path)
    secret = "must-not-appear-in-a-response"

    with TestClient(main_module.create_app(settings)) as client:
        created = client.put("/api/settings", json={"plex_token": secret})
        preserved = client.put("/api/settings", json={"plex_token": ""})
        fetched = client.get("/api/settings")
        cleared = client.put("/api/settings", json={"clear_plex_token": True})

    assert created.status_code == 200
    assert created.json()["plex_token_configured"] is True
    assert preserved.json()["plex_token_configured"] is True
    assert fetched.json()["plex_token_configured"] is True
    assert cleared.json()["plex_token_configured"] is False
    assert secret not in created.text + preserved.text + fetched.text + cleared.text
    assert "plex_token" not in fetched.json()


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
