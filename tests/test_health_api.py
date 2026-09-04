from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

import mediaclipmakarr.main as main_module
from mediaclipmakarr.config import Settings
from mediaclipmakarr.health import MediaToolInspection
from mediaclipmakarr.plex import PlexSession, PlexSessionSnapshot, snapshot_sse_payload


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
    assert payload["database"]["details"]["schema_revision"] == (
        "0007_immich_upload_active_unique"
    )
    assert payload["application"]["details"]["exclusive_lock"] is True
    serialized = response.text
    assert str(tmp_path) not in serialized
    assert spa_response.status_code == 200
    assert "SPA shell" in spa_response.text
    assert bare_api_response.status_code == 404
    assert missing_api_response.status_code == 404


def test_session_snapshot_is_in_memory_and_serializes_for_initial_sse_event(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    settings = Settings(
        _env_file=None,
        private_data_dir=tmp_path / "private",
        work_dir=tmp_path / "work",
        clip_dir=tmp_path / "clips",
        source_dirs=[source],
        frontend_dist_dir=tmp_path / "missing-frontend",
    )

    async def healthy_media_tools(_settings):
        return MediaToolInspection(
            status="ok",
            message="Media tools are ready.",
            details={"identity_ok": True, "libx264": True, "aac": True},
        )

    monkeypatch.setattr(main_module, "inspect_media_tools", healthy_media_tools)
    with TestClient(main_module.create_app(settings)) as client:
        client.app.state.plex_session_poller._snapshot = PlexSessionSnapshot(
            status="ok",
            message="Active Plex video sessions loaded.",
            sampled_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
            sessions=[
                PlexSession(
                    session_identity="plex-session:living-room",
                    media_identity="plex-media:movie",
                    title="A Movie",
                    media_type="movie",
                    plex_user="Alice",
                    player="Living Room",
                    state="playing",
                    position_ms=1_000,
                    duration_ms=10_000,
                    sampled_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
                    plex_rating_key="501",
                    plex_media_key="media-501",
                    plex_part_id="part-501",
                    plex_part_key="/library/parts/501",
                    plex_part_file="/plex/private/Movie.mkv",
                )
            ],
        )
        snapshot_response = client.get("/api/sessions")

    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.json()
    assert snapshot["status"] == "ok"
    assert "plex_part_file" not in snapshot["sessions"][0]
    assert "/plex/private/Movie.mkv" not in snapshot_response.text
    sse_payload = snapshot_sse_payload(
        client.app.state.plex_session_poller.snapshot
    )
    assert sse_payload.startswith("event: snapshot\ndata: ")
    assert '"status":"ok"' in sse_payload
    assert "plex_part_file" not in sse_payload
    assert "/plex/private/Movie.mkv" not in sse_payload
