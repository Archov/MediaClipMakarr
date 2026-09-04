from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import mediaclipmakarr.api.clips as clips_api
import mediaclipmakarr.main as main_module
from mediaclipmakarr.application_settings import EffectiveApplicationSettings
from mediaclipmakarr.clips import (
    ClipCreateRequest,
    ClipCreateValidationError,
    get_clip,
    insert_clip,
    set_clip_immich_asset_id,
    validate_clip_create_request,
)
from mediaclipmakarr.concurrency import BlockingIOExecutor
from mediaclipmakarr.config import Settings
from mediaclipmakarr.database import create_database_engine, upgrade_database
from mediaclipmakarr.health import MediaToolInspection
from mediaclipmakarr.immich import (
    ImmichAssetNotFoundError,
    ImmichInvalidResponseError,
)
from mediaclipmakarr.jobs import claim_next_job
from mediaclipmakarr.plex import PlexSession, PlexSessionSnapshot
from mediaclipmakarr.source_media import (
    MediaStreamIdentity,
    ResolvedSourceMedia,
    SourceFingerprint,
)

IMMICH_URL = "http://immich.example:2283"


class _StubJobEvents:
    async def publish(self, job_id, snapshot):
        return None


class _StubJobRunner:
    def wake(self):
        return None


def _effective_settings(**overrides: object) -> EffectiveApplicationSettings:
    base: dict[str, object] = dict(
        plex_url="",
        plex_token=None,
        source_path_mappings=[],
        timezone="UTC",
        timezone_configured=False,
        x264_preset="veryfast",
        environment_managed={},
        immich_url=IMMICH_URL,
        immich_api_key="test-key",
    )
    base.update(overrides)
    return EffectiveApplicationSettings(**base)  # type: ignore[arg-type]


def _build_clips_app(settings: Settings, engine, effective) -> FastAPI:
    app = FastAPI()
    app.state.database_engine = engine
    app.state.effective_application_settings = effective
    app.state.job_events = _StubJobEvents()
    app.state.job_runner = _StubJobRunner()
    app.state.blocking_io = BlockingIOExecutor(max_workers=1)
    app.include_router(clips_api.build_router(settings))
    return app


def _seed_clip(tmp_path: Path, *, clip_id: str = "clip-one", title: str = "Pilot"):
    database_path = tmp_path / "application.db"
    clip_root = tmp_path / "clips"
    source = clip_root / "TV Shows" / f"{title}.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"clip bytes")
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    now = datetime(2026, 8, 31, 12, 0)
    stat = source.stat()
    asyncio.run(
        insert_clip(
            engine,
            {
                "id": clip_id,
                "title": title,
                "automatic_title": title,
                "library": "TV Shows",
                "media_type": "episode",
                "file_path": str(source),
                "duration_ms": 10_000,
                "revision": 1,
                "source_start_ms": 1_000,
                "source_end_ms": 11_000,
                "source_path": "D:/read-only/source.mkv",
                "source_size_bytes": 999,
                "source_modified_at": now,
                "selected_audio_stream_index": 1,
                "render_plan_hash": "a" * 64,
                "created_at": now,
                "updated_at": now,
                "show_name": "Example Show",
                "episode_title": title,
                "season_number": 1,
                "episode_number": 1,
                "file_size_bytes": stat.st_size,
                "file_modified_ns": stat.st_mtime_ns,
            },
        )
    )
    return engine, clip_root


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


def make_source_media(tmp_path) -> ResolvedSourceMedia:
    return ResolvedSourceMedia(
        plex_path="/plex/Example.mkv",
        local_path=str(tmp_path / "source" / "Example.mkv"),
        fingerprint=SourceFingerprint(
            size_bytes=123,
            modified_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        ),
        duration_ms=120_000,
        video_streams=[],
        audio_streams=[
            MediaStreamIdentity(stream_index=1, codec_type="audio", codec_name="aac")
        ],
        subtitle_streams=[],
        selected_audio_stream=MediaStreamIdentity(
            stream_index=1, codec_type="audio", codec_name="aac"
        ),
    )


def test_valid_clip_create_request_preserves_integer_millisecond_range() -> None:
    request = ClipCreateRequest(
        session_identity="plex-session:living-room",
        media_identity="plex-media:episode-1",
        start_ms=12_345,
        end_ms=67_890,
    )

    result = validate_clip_create_request(request, make_snapshot(make_session()))

    assert result.valid is True
    assert result.start_ms == 12_345
    assert result.end_ms == 67_890
    assert result.duration_ms == 55_545


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

    async def source_media(*_args, **_kwargs):
        return make_source_media(tmp_path)

    monkeypatch.setattr(main_module, "inspect_media_tools", healthy_media_tools)
    monkeypatch.setattr(clips_api, "resolve_and_probe_source_media", source_media)
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

    assert valid_response.status_code == 200
    assert valid_response.json()["state"] in {"QUEUED", "RUNNING", "FAILED"}
    assert valid_response.json()["type"] == "clip_create"
    assert invalid_response.status_code == 422
    assert invalid_response.json()["detail"]["code"] == "CLIP_RANGE_ORDER"


def test_clip_media_is_served_inline_with_byte_ranges(tmp_path, monkeypatch) -> None:
    clip_dir = tmp_path / "clips"
    clip_dir.mkdir()
    clip_path = clip_dir / "Example Clip.mp4"
    clip_path.write_bytes(b"0123456789")
    settings = Settings(_env_file=None, clip_dir=clip_dir)

    async def clip(_engine, _clip_id, _clip_root):
        return {
            "id": "clip-1",
            "title": "Example Clip",
            "file_path": str(clip_path),
        }

    monkeypatch.setattr(clips_api, "get_clip", clip)
    app = FastAPI()
    app.state.database_engine = object()
    app.include_router(clips_api.build_router(settings))

    with TestClient(app) as client:
        play_response = client.get(
            "/api/clips/clip-1/media",
            headers={"Range": "bytes=2-5"},
        )
        download_response = client.get("/api/clips/clip-1/download")

    assert play_response.status_code == 206
    assert play_response.content == b"2345"
    assert play_response.headers["content-type"] == "video/mp4"
    assert play_response.headers["accept-ranges"] == "bytes"
    assert play_response.headers["content-range"] == "bytes 2-5/10"
    assert play_response.headers["content-disposition"].startswith("inline;")
    assert download_response.headers["content-disposition"].startswith("attachment;")


def test_check_immich_asset_returns_ok_when_asset_found(tmp_path, monkeypatch) -> None:
    engine, clip_root = _seed_clip(tmp_path)
    asyncio.run(set_clip_immich_asset_id(engine, "clip-one", "asset-1", IMMICH_URL))
    settings = Settings(_env_file=None, clip_dir=clip_root)

    async def fake_read(asset_id, url, api_key):
        return {"id": asset_id}

    monkeypatch.setattr(clips_api, "read_immich_asset", fake_read)
    app = _build_clips_app(settings, engine, _effective_settings())
    with TestClient(app) as client:
        response = client.post("/api/clips/clip-one/immich-check")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["open_url"] == f"{IMMICH_URL}/photos/asset-1"


def test_check_immich_asset_returns_missing_permission_without_asset_read(
    tmp_path, monkeypatch
) -> None:
    engine, clip_root = _seed_clip(tmp_path)
    asyncio.run(set_clip_immich_asset_id(engine, "clip-one", "asset-1", IMMICH_URL))
    settings = Settings(_env_file=None, clip_dir=clip_root)

    async def fake_read(asset_id, url, api_key):
        raise ImmichAssetNotFoundError(f"Immich asset {asset_id} no longer exists.")

    async def fake_permissions(url, api_key):
        return ["asset.upload"]

    monkeypatch.setattr(clips_api, "read_immich_asset", fake_read)
    monkeypatch.setattr(clips_api, "fetch_immich_api_key_permissions", fake_permissions)
    app = _build_clips_app(settings, engine, _effective_settings())
    with TestClient(app) as client:
        response = client.post("/api/clips/clip-one/immich-check")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "missing_permission"
    assert body["settings_url"] == f"{IMMICH_URL}/user-settings?isOpen=api-keys"

    # A missing-permission result never confirms the asset is actually gone,
    # so the association must be left alone.
    untouched = asyncio.run(get_clip(engine, "clip-one", clip_root))
    assert untouched is not None
    assert untouched["immich_asset_id"] == "asset-1"


def test_check_immich_asset_returns_asset_missing_when_permission_present(
    tmp_path, monkeypatch
) -> None:
    engine, clip_root = _seed_clip(tmp_path)
    asyncio.run(set_clip_immich_asset_id(engine, "clip-one", "asset-1", IMMICH_URL))
    settings = Settings(_env_file=None, clip_dir=clip_root)

    async def fake_read(asset_id, url, api_key):
        raise ImmichAssetNotFoundError(f"Immich asset {asset_id} no longer exists.")

    async def fake_permissions(url, api_key):
        return ["asset.read", "asset.upload"]

    monkeypatch.setattr(clips_api, "read_immich_asset", fake_read)
    monkeypatch.setattr(clips_api, "fetch_immich_api_key_permissions", fake_permissions)
    app = _build_clips_app(settings, engine, _effective_settings())
    with TestClient(app) as client:
        response = client.post("/api/clips/clip-one/immich-check")

    assert response.status_code == 200
    assert response.json()["status"] == "asset_missing"

    cleared = asyncio.run(get_clip(engine, "clip-one", clip_root))
    assert cleared is not None
    assert cleared["immich_asset_id"] is None
    assert cleared["immich_server_url"] is None


def test_check_immich_asset_rejects_a_clip_not_linked_to_the_current_server(
    tmp_path,
) -> None:
    engine, clip_root = _seed_clip(tmp_path)
    settings = Settings(_env_file=None, clip_dir=clip_root)
    app = _build_clips_app(settings, engine, _effective_settings())
    with TestClient(app) as client:
        response = client.post("/api/clips/clip-one/immich-check")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "IMMICH_NOT_LINKED"


def test_check_immich_asset_requires_immich_configured(tmp_path) -> None:
    engine, clip_root = _seed_clip(tmp_path)
    asyncio.run(set_clip_immich_asset_id(engine, "clip-one", "asset-1", IMMICH_URL))
    settings = Settings(_env_file=None, clip_dir=clip_root)
    app = _build_clips_app(
        settings, engine, _effective_settings(immich_url="", immich_api_key=None)
    )
    with TestClient(app) as client:
        response = client.post("/api/clips/clip-one/immich-check")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "IMMICH_NOT_CONFIGURED"


def test_reupload_clip_to_immich_clears_the_stale_association_then_enqueues(
    tmp_path,
) -> None:
    engine, clip_root = _seed_clip(tmp_path)
    asyncio.run(set_clip_immich_asset_id(engine, "clip-one", "asset-old", IMMICH_URL))
    settings = Settings(_env_file=None, clip_dir=clip_root)
    app = _build_clips_app(settings, engine, _effective_settings())
    with TestClient(app) as client:
        response = client.post("/api/clips/clip-one/immich-reupload")

    assert response.status_code == 200
    assert response.json()["type"] == "immich_upload"
    clip = asyncio.run(get_clip(engine, "clip-one", clip_root))
    assert clip is not None
    assert clip["immich_asset_id"] is None
    claimed = asyncio.run(claim_next_job(engine, "run-token"))
    assert claimed is not None
    assert claimed.type == "immich_upload"


def test_remove_clip_reports_a_warning_when_remote_delete_fails(
    tmp_path, monkeypatch
) -> None:
    engine, clip_root = _seed_clip(tmp_path)
    asyncio.run(set_clip_immich_asset_id(engine, "clip-one", "asset-1", IMMICH_URL))
    settings = Settings(_env_file=None, clip_dir=clip_root)

    async def fake_delete(asset_id, url, api_key):
        raise ImmichInvalidResponseError("Immich returned HTTP 500.")

    monkeypatch.setattr(clips_api, "delete_immich_asset", fake_delete)
    app = _build_clips_app(settings, engine, _effective_settings())
    with TestClient(app) as client:
        response = client.request(
            "DELETE",
            "/api/clips/clip-one",
            json={"expected_revision": 1, "delete_from_immich": True},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert any("Immich" in warning for warning in body["cleanup_warnings"])
    assert asyncio.run(get_clip(engine, "clip-one", clip_root)) is None


def test_remove_clip_reports_no_warning_when_remote_asset_is_already_gone(
    tmp_path, monkeypatch
) -> None:
    engine, clip_root = _seed_clip(tmp_path)
    asyncio.run(set_clip_immich_asset_id(engine, "clip-one", "asset-1", IMMICH_URL))
    settings = Settings(_env_file=None, clip_dir=clip_root)

    async def fake_delete(asset_id, url, api_key):
        raise ImmichAssetNotFoundError(f"Immich asset {asset_id} no longer exists.")

    monkeypatch.setattr(clips_api, "delete_immich_asset", fake_delete)
    app = _build_clips_app(settings, engine, _effective_settings())
    with TestClient(app) as client:
        response = client.request(
            "DELETE",
            "/api/clips/clip-one",
            json={"expected_revision": 1, "delete_from_immich": True},
        )

    assert response.status_code == 200
    assert response.json()["cleanup_warnings"] == []


def test_remove_clip_skips_remote_delete_when_not_requested(tmp_path, monkeypatch) -> None:
    engine, clip_root = _seed_clip(tmp_path)
    asyncio.run(set_clip_immich_asset_id(engine, "clip-one", "asset-1", IMMICH_URL))
    settings = Settings(_env_file=None, clip_dir=clip_root)
    calls: list[str] = []

    async def fake_delete(asset_id, url, api_key):
        calls.append(asset_id)

    monkeypatch.setattr(clips_api, "delete_immich_asset", fake_delete)
    app = _build_clips_app(settings, engine, _effective_settings())
    with TestClient(app) as client:
        response = client.request(
            "DELETE", "/api/clips/clip-one", json={"expected_revision": 1}
        )

    assert response.status_code == 200
    assert calls == []


def test_remove_clip_skips_remote_delete_when_clip_was_not_linked(
    tmp_path, monkeypatch
) -> None:
    engine, clip_root = _seed_clip(tmp_path)
    settings = Settings(_env_file=None, clip_dir=clip_root)
    calls: list[str] = []

    async def fake_delete(asset_id, url, api_key):
        calls.append(asset_id)

    monkeypatch.setattr(clips_api, "delete_immich_asset", fake_delete)
    app = _build_clips_app(settings, engine, _effective_settings())
    with TestClient(app) as client:
        response = client.request(
            "DELETE",
            "/api/clips/clip-one",
            json={"expected_revision": 1, "delete_from_immich": True},
        )

    assert response.status_code == 200
    assert calls == []
