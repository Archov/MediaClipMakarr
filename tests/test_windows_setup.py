from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from mediaclipmakarr.config import ENV_FILE_VARIABLE, Settings, load_settings
from scripts import windows_setup
from scripts.windows_setup import DownloadAsset, PathMapping, WindowsProfile


def _tool_pair(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True)
    ffmpeg = root / "ffmpeg.exe"
    ffprobe = root / "ffprobe.exe"
    ffmpeg.write_bytes(b"test")
    ffprobe.write_bytes(b"test")
    return ffmpeg, ffprobe


def test_windows_profile_round_trip_preserves_paths_and_mapping_json(tmp_path) -> None:
    source = tmp_path / "media files"
    source.mkdir()
    ffmpeg, ffprobe = _tool_pair(tmp_path / "tools")
    profile = WindowsProfile(
        data_root=tmp_path / "application data",
        source_dirs=[source],
        mappings=[PathMapping("/srv/plex/media", source)],
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        api_port=8123,
        web_port=5123,
    )
    profile_path = tmp_path / ".env.windows"

    windows_setup.write_windows_profile(profile, profile_path)
    first_profile = profile_path.read_text(encoding="utf-8")
    windows_setup.write_windows_profile(profile, profile_path)
    loaded = windows_setup.load_windows_profile(profile_path)
    settings = Settings(_env_file=profile_path)

    assert loaded.data_root == profile.data_root.resolve()
    assert loaded.source_dirs == [source.resolve()]
    assert loaded.mappings == [PathMapping("/srv/plex/media", source.resolve())]
    assert settings.source_dirs == [source.resolve()]
    assert json.loads(settings.source_path_mappings or "") == [
        {"plex_prefix": "/srv/plex/media", "local_prefix": source.resolve().as_posix()}
    ]
    assert settings.dev_api_port == 8123
    assert settings.dev_web_port == 5123
    assert profile_path.with_name(".env.windows.bak").read_text(encoding="utf-8") == first_profile


def test_windows_profile_rejects_mapping_outside_approved_source(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    ffmpeg, ffprobe = _tool_pair(tmp_path / "tools")
    profile = WindowsProfile(
        data_root=tmp_path / "data",
        source_dirs=[source],
        mappings=[PathMapping("/plex/media", outside)],
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
    )

    with pytest.raises(ValueError, match="inside an approved source folder"):
        windows_setup.validate_windows_profile(profile)


def test_explicit_environment_profile_is_loaded(monkeypatch, tmp_path) -> None:
    profile_path = tmp_path / ".env.windows"
    profile_path.write_text("MCM_DEV_API_PORT=8234\n", encoding="utf-8")
    monkeypatch.setenv(ENV_FILE_VARIABLE, str(profile_path))

    assert load_settings().dev_api_port == 8234


class _DownloadResponse(io.BytesIO):
    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.headers = {"Content-Length": str(len(content))}


def test_verified_ffmpeg_download_extracts_expected_tool_pair(monkeypatch, tmp_path) -> None:
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("jellyfin/bin/ffmpeg.exe", b"ffmpeg")
        archive.writestr("jellyfin/bin/ffprobe.exe", b"ffprobe")
        archive.writestr("jellyfin/bin/avcodec.dll", b"dll")
    content = archive_buffer.getvalue()
    asset = DownloadAsset(
        architecture="test",
        url="https://example.invalid/jellyfin-ffmpeg.zip",
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
    )
    progress: list[tuple[int, int]] = []
    monkeypatch.setattr(
        windows_setup.urllib.request,
        "urlopen",
        lambda _request, timeout: _DownloadResponse(content),
    )

    destination = tmp_path / "installed"
    ffmpeg, ffprobe = windows_setup.download_jellyfin_ffmpeg(
        destination,
        asset=asset,
        progress=lambda received, total: progress.append((received, total)),
    )

    assert ffmpeg.read_bytes() == b"ffmpeg"
    assert ffprobe.read_bytes() == b"ffprobe"
    assert (destination / "avcodec.dll").read_bytes() == b"dll"
    assert progress[-1] == (len(content), len(content))


def test_ffmpeg_download_rejects_checksum_mismatch(monkeypatch, tmp_path) -> None:
    content = b"not a zip"
    asset = DownloadAsset(
        architecture="test",
        url="https://example.invalid/jellyfin-ffmpeg.zip",
        sha256="0" * 64,
        size=len(content),
    )
    monkeypatch.setattr(
        windows_setup.urllib.request,
        "urlopen",
        lambda _request, timeout: _DownloadResponse(content),
    )
    destination = tmp_path / "installed"

    with pytest.raises(ValueError, match="SHA-256"):
        windows_setup.download_jellyfin_ffmpeg(destination, asset=asset)

    assert not destination.exists()


@pytest.mark.parametrize(
    ("machine", "architecture"),
    [("AMD64", "win64"), ("x86_64", "win64"), ("ARM64", "winarm64")],
)
def test_download_asset_matches_windows_architecture(machine, architecture) -> None:
    assert windows_setup.select_download_asset(machine).architecture == architecture
