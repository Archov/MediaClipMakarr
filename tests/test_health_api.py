from __future__ import annotations

from fastapi.testclient import TestClient

import mediaclipmakarr.main as main_module
from mediaclipmakarr.config import Settings
from mediaclipmakarr.health import MediaToolInspection


def test_health_reports_bootstrap_state_without_paths(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<h1>SPA shell</h1>", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        private_data_dir=tmp_path / "private",
        work_dir=tmp_path / "work",
        clip_dir=tmp_path / "clips",
        source_dirs=[source],
        frontend_dist_dir=frontend,
    )

    async def healthy_media_tools(_settings):
        return MediaToolInspection(
            status="ok",
            message="Media tools are ready.",
            details={"identity_ok": True, "libx264": True, "aac": True},
        )

    monkeypatch.setattr(main_module, "inspect_media_tools", healthy_media_tools)
    with TestClient(main_module.create_app(settings)) as client:
        response = client.get("/api/health")
        spa_response = client.get("/clips/future-route")
        bare_api_response = client.get("/api")
        missing_api_response = client.get("/api/not-a-real-endpoint")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"]["details"]["schema_revision"] == "0001_bootstrap"
    assert payload["application"]["details"]["exclusive_lock"] is True
    serialized = response.text
    assert str(tmp_path) not in serialized
    assert spa_response.status_code == 200
    assert "SPA shell" in spa_response.text
    assert bare_api_response.status_code == 404
    assert missing_api_response.status_code == 404
