from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mediaclipmakarr.api.clip_trim as clip_trim_api
from mediaclipmakarr.api.clip_trim import (
    ClipMediaProbeError,
    frame_rate_from_probe,
    probe_clip_frame_rate,
)
from mediaclipmakarr.concurrency import BlockingIOExecutor, MediaProcessGate
from mediaclipmakarr.config import Settings
from mediaclipmakarr.hdr import HdrCapabilities, VideoColorMetadata
from mediaclipmakarr.jobs import JobSnapshot
from mediaclipmakarr.media_renderer import RenderedClipFile
from mediaclipmakarr.source_media import (
    MediaCapabilities,
    MediaStreamIdentity,
    ResolvedSourceMedia,
    SourceFingerprint,
    SourceMediaError,
    SubtitleSelection,
    VideoStreamIdentity,
)
from mediaclipmakarr.subprocesses import CommandFailedError, CommandResult


class FakeJobEvents:
    async def publish(self, _job_id: str, _job: JobSnapshot) -> None:
        return None


class FakeJobRunner:
    def wake(self) -> None:
        return None


def _app(settings: Settings) -> FastAPI:
    app = FastAPI()
    app.state.database_engine = object()
    app.state.blocking_io = BlockingIOExecutor(max_workers=1)
    app.state.media_process_gate = MediaProcessGate()
    app.state.job_events = FakeJobEvents()
    app.state.job_runner = FakeJobRunner()

    class Effective:
        x264_preset = "veryfast"
        video_decode = "cpu"
        video_tonemap = "cpu"
        video_encoder = "cpu_x264"
        video_quality = 18
        video_max_resolution = "1080p"
        video_max_fps = 60

    app.state.effective_application_settings = Effective()
    app.include_router(clip_trim_api.build_router(settings))
    return app


def _clip(tmp_path: Path, **overrides: object) -> dict[str, object]:
    managed = tmp_path / "Example.mp4"
    managed.write_bytes(b"managed clip")
    source = tmp_path / "Example.mkv"
    source.write_bytes(b"original source")
    stat = source.stat()
    clip: dict[str, object] = {
        "id": "clip-1",
        "title": "Example",
        "library": "Movies",
        "media_type": "movie",
        "file_path": str(managed),
        "duration_ms": 10_000,
        "revision": 1,
        "source_start_ms": 50_000,
        "source_end_ms": 60_000,
        "source_path": str(source),
        "source_size_bytes": stat.st_size,
        "source_modified_at": datetime.fromtimestamp(stat.st_mtime, UTC),
        "selected_audio_stream_index": 4,
        "created_at": datetime(2026, 9, 1, 12, 0),
        "automatic_title": "Example",
        "custom_title": None,
    }
    clip.update(overrides)
    return clip


def _original_source(source_path: Path, *, duration_ms: int = 120_000) -> ResolvedSourceMedia:
    stat = source_path.stat()
    audio = MediaStreamIdentity(stream_index=4, codec_type="audio", codec_name="aac")
    return ResolvedSourceMedia(
        plex_path=str(source_path),
        local_path=str(source_path),
        fingerprint=SourceFingerprint(
            size_bytes=stat.st_size, modified_at=datetime.fromtimestamp(stat.st_mtime, UTC)
        ),
        duration_ms=duration_ms,
        video_streams=[
            VideoStreamIdentity(
                stream_index=0,
                codec_type="video",
                codec_name="h264",
                width=1920,
                height=1080,
                color=VideoColorMetadata(color_transfer="bt709"),
            )
        ],
        audio_streams=[audio],
        subtitle_streams=[],
        capabilities=MediaCapabilities(
            duration_ms=duration_ms,
            frame_rate=24.0,
            video_tracks=[],
            audio_tracks=[],
            subtitle_tracks=[],
            attachment_tracks=[],
            default_audio_stream_index=4,
            hdr=HdrCapabilities(),
        ),
        selected_audio_stream=audio,
        selected_subtitle=SubtitleSelection(),
    )


def test_frame_rate_prefers_average_then_nominal() -> None:
    assert frame_rate_from_probe(
        {"streams": [{"avg_frame_rate": "24000/1001", "r_frame_rate": "25/1"}]}
    ) == pytest.approx(23.976, abs=0.001)
    assert frame_rate_from_probe(
        {"streams": [{"avg_frame_rate": "0/0", "r_frame_rate": "30000/1001"}]}
    ) == pytest.approx(29.970, abs=0.001)


def test_frame_rate_can_be_unreported_but_rejects_invalid_payload() -> None:
    assert frame_rate_from_probe({"streams": [{}]}) is None
    assert frame_rate_from_probe({"streams": []}) is None
    with pytest.raises(ClipMediaProbeError):
        frame_rate_from_probe({})


@pytest.mark.asyncio
async def test_probe_clip_frame_rate_uses_a_bounded_video_only_probe(tmp_path: Path) -> None:
    clip_path = tmp_path / "managed clip.mp4"
    clip_path.write_bytes(b"video")
    captured: list[str] = []

    async def runner(argv, **kwargs):
        captured.extend(str(value) for value in argv)
        assert kwargs["timeout_seconds"] == 10
        return CommandResult(
            tuple(captured),
            0,
            json.dumps({"streams": [{"avg_frame_rate": "24/1"}]}),
            "",
        )

    result = await probe_clip_frame_rate(
        clip_path, Settings(_env_file=None), runner=runner
    )

    assert result == 24
    assert captured[-1] == str(clip_path)
    assert captured[captured.index("-select_streams") + 1] == "v:0"


@pytest.mark.asyncio
async def test_probe_failure_is_a_stable_domain_error(tmp_path: Path) -> None:
    async def runner(_argv, **_kwargs):
        raise CommandFailedError("ffprobe", 1, "bad media")

    with pytest.raises(ClipMediaProbeError, match="could not inspect"):
        await probe_clip_frame_rate(
            tmp_path / "clip.mp4", Settings(_env_file=None), runner=runner
        )


def test_trim_info_returns_opening_revision_and_nominal_rate(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(_env_file=None, clip_dir=tmp_path)
    clip_path = tmp_path / "Movies" / "Example.mp4"
    clip_path.parent.mkdir(parents=True)
    clip_path.write_bytes(b"video")

    async def get_clip(*_args, **_kwargs):
        return {
            "id": "clip-1",
            "title": "Example",
            "duration_ms": 12_345,
            "revision": 7,
            "file_path": str(clip_path),
        }

    async def probe(*_args, **_kwargs):
        return 24000 / 1001

    monkeypatch.setattr(clip_trim_api, "get_clip", get_clip)
    monkeypatch.setattr(clip_trim_api, "probe_clip_frame_rate", probe)
    app = FastAPI()
    app.state.database_engine = object()
    app.include_router(clip_trim_api.build_router(settings))

    with TestClient(app) as client:
        response = client.get("/api/clips/clip-1/trim-info")

    assert response.status_code == 200
    assert response.json() == {
        "id": "clip-1",
        "title": "Example",
        "duration_ms": 12_345,
        "revision": 7,
        "play_url": "/api/clips/clip-1/media",
        "frame_rate": pytest.approx(23.976, abs=0.001),
    }


def test_trim_info_does_not_probe_an_unmanaged_or_missing_clip(monkeypatch) -> None:
    settings = Settings(_env_file=None)

    async def get_clip(*_args, **_kwargs):
        return None

    async def unexpected_probe(*_args, **_kwargs):
        raise AssertionError("unmanaged media must not be probed")

    monkeypatch.setattr(clip_trim_api, "get_clip", get_clip)
    monkeypatch.setattr(clip_trim_api, "probe_clip_frame_rate", unexpected_probe)
    app = FastAPI()
    app.state.database_engine = object()
    app.include_router(clip_trim_api.build_router(settings))

    with TestClient(app) as client:
        response = client.get("/api/clips/not-managed/trim-info")

    assert response.status_code == 404


def test_source_tracks_reports_available_with_track_lists_and_extend_room(
    monkeypatch, tmp_path: Path
) -> None:
    settings = Settings(_env_file=None, work_dir=tmp_path / "work")
    clip = _clip(tmp_path)

    async def get_clip(*_args, **_kwargs):
        return clip

    async def resolve(*_args, **kwargs):
        assert kwargs["audio_stream_index"] == 4
        assert kwargs["audio_disabled"] is False
        return _original_source(Path(str(clip["source_path"])), duration_ms=90_000), None

    monkeypatch.setattr(clip_trim_api, "get_clip", get_clip)
    monkeypatch.setattr(clip_trim_api, "read_embedded_render_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(clip_trim_api, "_resolve_extended_render_source", resolve)

    with TestClient(_app(settings)) as client:
        response = client.get("/api/clips/clip-1/source-tracks")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    # source_start_ms=50_000, source_end_ms=60_000, source duration 90_000
    assert body["max_extend_before_ms"] == 50_000
    assert body["max_extend_after_ms"] == 30_000


def test_source_tracks_does_not_force_resolve_an_external_subtitle_as_embedded(
    monkeypatch, tmp_path: Path
) -> None:
    """Regression: an external (Plex sidecar) subtitle selection carries a
    synthetic stream index that doesn't correspond to any real embedded
    track in the source. Passing it through as if it were an embedded index
    makes probe_original_source_media fail to find it, incorrectly
    reporting an otherwise-valid clip as unavailable for extend/track-switch
    (see is_extended_trim_request's SUBTITLE_STREAM_UNAVAILABLE case)."""
    settings = Settings(_env_file=None, work_dir=tmp_path / "work")
    clip = _clip(tmp_path)
    captured_kwargs: dict[str, object] = {}
    embedded = {
        "selectedSubtitle": {
            "enabled": True,
            "stream": {
                "stream_index": 99,
                "codec_type": "subtitle",
                "codec_name": "srt",
                "language": "eng",
                "title": None,
                "filename": None,
                "mime_type": None,
            },
            "strategy": "external_text",
            "external_url": "http://plex.example:32400/library/streams/501.srt",
        }
    }

    async def get_clip(*_args, **_kwargs):
        return clip

    async def resolve(*_args, **kwargs):
        captured_kwargs.update(kwargs)
        return _original_source(Path(str(clip["source_path"])), duration_ms=90_000), None

    monkeypatch.setattr(clip_trim_api, "get_clip", get_clip)
    monkeypatch.setattr(clip_trim_api, "read_embedded_render_metadata", lambda *_a, **_k: embedded)
    monkeypatch.setattr(clip_trim_api, "_resolve_extended_render_source", resolve)

    with TestClient(_app(settings)) as client:
        response = client.get("/api/clips/clip-1/source-tracks")

    assert response.status_code == 200
    assert response.json()["available"] is True
    assert captured_kwargs["subtitle_stream_index"] is None
    assert captured_kwargs["subtitles_enabled"] is False


def test_source_tracks_reports_unavailable_when_source_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    settings = Settings(_env_file=None, work_dir=tmp_path / "work")
    clip = _clip(tmp_path)

    async def get_clip(*_args, **_kwargs):
        return clip

    async def resolve(*_args, **_kwargs):
        raise SourceMediaError("SOURCE_PATH_MISSING", "The original source media is no longer available.")

    monkeypatch.setattr(clip_trim_api, "get_clip", get_clip)
    monkeypatch.setattr(clip_trim_api, "read_embedded_render_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(clip_trim_api, "_resolve_extended_render_source", resolve)

    with TestClient(_app(settings)) as client:
        response = client.get("/api/clips/clip-1/source-tracks")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert "no longer available" in body["unavailable_reason"]


def test_save_trim_uses_the_fast_path_when_in_bounds_and_unchanged(
    monkeypatch, tmp_path: Path
) -> None:
    settings = Settings(_env_file=None, work_dir=tmp_path / "work")
    clip = _clip(tmp_path)
    calls: list[str] = []

    async def get_clip(*_args, **_kwargs):
        return clip

    async def probe_managed(*_args, **_kwargs):
        calls.append("probe_managed")
        return _original_source(Path(str(clip["file_path"])), duration_ms=10_000)

    async def unexpected_extended(*_args, **_kwargs):
        raise AssertionError("the fast path must not resolve the original source")

    def fake_build_trim_render_plan(*_args, **_kwargs):
        calls.append("build_trim_render_plan")
        raise clip_trim_api.ClipEditError("STOP_HERE", "test stop", retryable=False)

    monkeypatch.setattr(clip_trim_api, "get_clip", get_clip)
    monkeypatch.setattr(clip_trim_api, "probe_managed_media_file", probe_managed)
    monkeypatch.setattr(clip_trim_api, "_resolve_extended_render_source", unexpected_extended)
    monkeypatch.setattr(clip_trim_api, "build_trim_render_plan", fake_build_trim_render_plan)

    with TestClient(_app(settings)) as client:
        response = client.post(
            "/api/clips/clip-1/trim",
            json={"start_ms": 0, "end_ms": 5_000, "expected_revision": 1, "mode": "new"},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "STOP_HERE"
    assert calls == ["probe_managed", "build_trim_render_plan"]


def test_save_trim_uses_the_extended_path_when_out_of_bounds(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(_env_file=None, work_dir=tmp_path / "work")
    clip = _clip(tmp_path)
    calls: list[str] = []

    async def get_clip(*_args, **_kwargs):
        return clip

    async def resolve(*_args, **_kwargs):
        calls.append("resolve_extended")
        return _original_source(Path(str(clip["source_path"])), duration_ms=120_000), Path(
            str(clip["source_path"])
        ).stat()

    async def unexpected_managed(*_args, **_kwargs):
        raise AssertionError("the extended path must not probe the managed clip file")

    def fake_build_extended(*_args, **_kwargs):
        calls.append("build_extended_trim_render_plan")
        raise clip_trim_api.ClipEditError("STOP_HERE", "test stop", retryable=False)

    monkeypatch.setattr(clip_trim_api, "get_clip", get_clip)
    monkeypatch.setattr(clip_trim_api, "probe_managed_media_file", unexpected_managed)
    monkeypatch.setattr(clip_trim_api, "_resolve_extended_render_source", resolve)
    monkeypatch.setattr(clip_trim_api, "build_extended_trim_render_plan", fake_build_extended)

    with TestClient(_app(settings)) as client:
        response = client.post(
            "/api/clips/clip-1/trim",
            # end_ms (12_000) exceeds the managed clip's own duration_ms (10_000)
            json={"start_ms": 0, "end_ms": 12_000, "expected_revision": 1, "mode": "new"},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "STOP_HERE"
    assert calls == ["resolve_extended", "build_extended_trim_render_plan"]


def test_create_trim_preview_renders_and_the_result_is_servable_then_deletable(
    monkeypatch, tmp_path: Path
) -> None:
    settings = Settings(_env_file=None, work_dir=tmp_path / "work")
    clip = _clip(tmp_path)
    token = "preview-token-1"

    async def get_clip(*_args, **_kwargs):
        return clip

    async def resolve(*_args, **_kwargs):
        return _original_source(Path(str(clip["source_path"])), duration_ms=120_000), Path(
            str(clip["source_path"])
        ).stat()

    async def fake_render(plan, _settings, *, progress):
        workdir = settings.resolved_work_dir / "jobs" / plan.job_id
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "rendered.mp4").write_bytes(b"fake rendered preview")
        return RenderedClipFile(path=workdir / "rendered.mp4", duration_ms=5_000)

    monkeypatch.setattr(clip_trim_api, "get_clip", get_clip)
    monkeypatch.setattr(clip_trim_api, "_resolve_extended_render_source", resolve)
    monkeypatch.setattr(clip_trim_api, "render_clip_file", fake_render)

    with TestClient(_app(settings)) as client:
        created = client.post(
            "/api/clips/clip-1/trim-preview",
            json={"start_ms": -5_000, "end_ms": 7_500, "preview_token": token},
        )
        assert created.status_code == 200
        body = created.json()
        assert body["duration_ms"] == 5_000
        assert body["play_url"] == f"/api/clips/clip-1/trim-preview/{token}"

        served = client.get(body["play_url"])
        assert served.status_code == 200
        assert served.content == b"fake rendered preview"
        assert served.headers["cache-control"] == "no-store"

        deleted = client.delete(body["play_url"])
        assert deleted.status_code == 204

        missing = client.get(body["play_url"])
        assert missing.status_code == 404


def test_create_trim_preview_rejects_an_unsafe_preview_token(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, work_dir=tmp_path / "work")

    with TestClient(_app(settings)) as client:
        response = client.post(
            "/api/clips/clip-1/trim-preview",
            json={"start_ms": 0, "end_ms": 5_000, "preview_token": "../../etc"},
        )

    assert response.status_code == 422
