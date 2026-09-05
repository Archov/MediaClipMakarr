from __future__ import annotations

import json
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
from mediaclipmakarr.config import Settings
from mediaclipmakarr.subprocesses import CommandFailedError, CommandResult


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
