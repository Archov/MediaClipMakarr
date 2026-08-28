from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

import mediaclipmakarr.main as main_module
from mediaclipmakarr.clips import (
    ClipCreateRequest,
    ClipCreateValidationError,
    validate_clip_create_request,
)
from mediaclipmakarr.config import Settings
from mediaclipmakarr.health import MediaToolInspection
from mediaclipmakarr.plex import PlexSession, PlexSessionSnapshot


def make_session(
    *,
    session_identity: str = "plex-session:living-room",
    media_identity: str = "plex-media:episode-1",
    duration_ms: int | None = 120_000,
) -> PlexSession:
    return PlexSession(
        session_identity=session_identity,
        media_identity=media_identity,
        title="Example Show - Pilot",
        media_type="episode",
        plex_user="Alice",
        player="Living Room",
        state="playing",
        position_ms=10_000,
        duration_ms=duration_ms,
        sampled_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        plex_rating_key="101",
        plex_media_key="201",
        plex_part_id="301",
    )


def make_snapshot(*sessions: PlexSession, status: str = "ok") -> PlexSessionSnapshot:
    return PlexSessionSnapshot(
        status=status,
        message="Active Plex video sessions loaded.",
        sampled_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        sessions=list(sessions),
    )


def test_valid_clip_create_request_preserves_integer_millisecond_range() -> None:
    request = ClipCreateRequest(
        session_identity="plex-session:living-room",
        media_identity="plex-media:episode-1",
        start_ms=12_345,
        end_ms=67_890,
    )

    accepted = validate_clip_create_request(request, make_snapshot(make_session()))

    assert accepted.accepted is True
    assert accepted.start_ms == 12_345
    assert accepted.end_ms == 67_890
    assert accepted.duration_ms == 55_545


def test_invalid_range_order_is_rejected_before_submission_work() -> None:
    request = ClipCreateRequest(
        session_identity="plex-session:living-room",
        media_identity="plex-media:episode-1",
        start_ms=5_000,
        end_ms=5_000,
    )

    try:
        validate_clip_create_request(request, make_snapshot(make_session()))
    except ClipCreateValidationError as error:
        assert error.error.code == "CLIP_RANGE_ORDER"
        assert error.status_code == 422
    else:
        raise AssertionError("Expected invalid range to be rejected.")


def test_stale_media_identity_is_rejected() -> None:
    request = ClipCreateRequest(
        session_identity="plex-session:living-room",
        media_identity="plex-media:episode-1",
        start_ms=1_000,
        end_ms=2_000,
    )

    try:
        validate_clip_create_request(
            request,
            make_snapshot(make_session(media_identity="plex-media:episode-2")),
        )
    except ClipCreateValidationError as error:
        assert error.error.code == "PLEX_MEDIA_CHANGED"
        assert error.status_code == 409
    else:
        raise AssertionError("Expected stale media identity to be rejected.")


def test_range_cannot_exceed_known_media_duration() -> None:
    request = ClipCreateRequest(
        session_identity="plex-session:living-room",
        media_identity="plex-media:episode-1",
        start_ms=119_000,
        end_ms=121_000,
    )

    try:
        validate_clip_create_request(request, make_snapshot(make_session()))
    except ClipCreateValidationError as error:
        assert error.error.code == "CLIP_RANGE_DURATION_EXCEEDED"
    else:
        raise AssertionError("Expected out-of-bounds range to be rejected.")


def test_clip_api_returns_structured_validation_errors(tmp_path, monkeypatch) -> None:
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
        client.app.state.plex_session_poller._snapshot = make_snapshot(make_session())

        valid_response = client.post(
            "/api/clips",
            json={
                "session_identity": "plex-session:living-room",
                "media_identity": "plex-media:episode-1",
                "start_ms": 12_345,
                "end_ms": 67_890,
            },
        )
        invalid_response = client.post(
            "/api/clips",
            json={
                "session_identity": "plex-session:living-room",
                "media_identity": "plex-media:episode-1",
                "start_ms": 67_890,
                "end_ms": 12_345,
            },
        )

    assert valid_response.status_code == 202
    assert valid_response.json()["duration_ms"] == 55_545
    assert invalid_response.status_code == 422
    assert invalid_response.json()["detail"]["code"] == "CLIP_RANGE_ORDER"
