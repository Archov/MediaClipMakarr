from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import mediaclipmakarr.api.plex as plex_api
from mediaclipmakarr.concurrency import MediaProcessGate
from mediaclipmakarr.config import Settings
from mediaclipmakarr.plex import PlexSession, PlexSessionSnapshot
from mediaclipmakarr.session_frames import RenderedSessionFrame


def active_session() -> PlexSession:
    return PlexSession(
        session_identity="session-1",
        media_identity="media-1",
        title="Movie",
        media_type="movie",
        plex_user=None,
        player=None,
        state="paused",
        position_ms=12_345,
        duration_ms=60_000,
        sampled_at=datetime(2026, 9, 2, tzinfo=UTC),
        plex_part_file="/plex/Movie.mkv",
    )


def test_session_frame_endpoint_exports_the_validated_full_resolution_variant(
    tmp_path, monkeypatch
) -> None:
    rendered_variants: list[str] = []

    async def fake_render_session_frame(
        _session,
        _position_ms,
        variant,
        _effective_settings,
        _settings,
        *,
        run_blocking,
    ):
        del run_blocking
        rendered_variants.append(variant)
        work_dir = tmp_path / "frame-work"
        work_dir.mkdir()
        output = work_dir / "export.png"
        output.write_bytes(b"png-frame")
        return RenderedSessionFrame(
            path=output,
            work_dir=work_dir,
            filename="frame-12345ms.png",
        )

    monkeypatch.setattr(plex_api, "render_session_frame", fake_render_session_frame)
    app = FastAPI()
    app.include_router(plex_api.build_router(Settings(_env_file=None)))
    app.state.plex_session_poller = SimpleNamespace(
        snapshot=PlexSessionSnapshot(
            status="ok",
            message="Active Plex video sessions loaded.",
            sampled_at=datetime(2026, 9, 2, tzinfo=UTC),
            sessions=[active_session()],
        )
    )
    app.state.media_process_gate = MediaProcessGate()
    app.state.effective_application_settings = object()
    app.state.blocking_io = SimpleNamespace(run=object())

    with TestClient(app) as client:
        response = client.get(
            "/api/sessions/session-1/frame",
            params={
                "media_identity": "media-1",
                "position_ms": 12_345,
                "download": "true",
            },
        )
        changed_media = client.get(
            "/api/sessions/session-1/frame",
            params={"media_identity": "media-2", "position_ms": 12_345},
        )

    assert response.status_code == 200
    assert response.content == b"png-frame"
    assert response.headers["content-type"] == "image/png"
    assert "attachment" in response.headers["content-disposition"]
    assert "frame-12345ms.png" in response.headers["content-disposition"]
    assert rendered_variants == ["export"]
    assert changed_media.status_code == 409
    assert changed_media.json()["detail"]["code"] == "PLEX_MEDIA_CHANGED"
