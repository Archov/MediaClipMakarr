from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mediaclipmakarr.clips import ClipCreateRequest
from mediaclipmakarr.config import Settings
from mediaclipmakarr.media_renderer import build_ffmpeg_clip_args
from mediaclipmakarr.plex import PlexSession
from mediaclipmakarr.render_plan import build_clip_render_plan
from mediaclipmakarr.source_media import (
    MediaStreamIdentity,
    ResolvedSourceMedia,
    SourceFingerprint,
    VideoColorMetadata,
    VideoStreamIdentity,
)


def _plan(tmp_path: Path, *, encoder: str = "cpu_x264", video_quality: int = 18):
    source_file = tmp_path / "Movie.mkv"
    source_file.write_bytes(b"media")
    source_media = ResolvedSourceMedia(
        plex_path="/plex/Movie.mkv",
        local_path=str(source_file),
        fingerprint=SourceFingerprint(size_bytes=5, modified_at=datetime.now(UTC)),
        duration_ms=10_000,
        video_streams=[
            VideoStreamIdentity(
                stream_index=0,
                codec_type="video",
                codec_name="h264",
                width=1280,
                height=720,
                color=VideoColorMetadata(color_transfer="bt709"),
            )
        ],
        audio_streams=[MediaStreamIdentity(stream_index=1, codec_type="audio", codec_name="aac")],
        subtitle_streams=[],
        selected_audio_stream=MediaStreamIdentity(
            stream_index=1, codec_type="audio", codec_name="aac"
        ),
    )
    session = PlexSession(
        session_identity="plex-session:living-room",
        media_identity="plex-media:movie",
        title="A Movie",
        media_type="movie",
        plex_user="Alice",
        player="Living Room",
        state="playing",
        position_ms=1_000,
        duration_ms=10_000,
        sampled_at=datetime.now(UTC),
    )
    return build_clip_render_plan(
        session=session,
        request=ClipCreateRequest(
            session_identity=session.session_identity,
            media_identity=session.media_identity,
            start_ms=1_000,
            end_ms=4_000,
        ),
        source_media=source_media,
        x264_preset="veryfast",
        encoder=encoder,
        video_quality=video_quality,
    )


def test_build_clip_render_plan_defaults_to_cpu_x264(tmp_path) -> None:
    plan = _plan(tmp_path)

    assert plan.encoder == "cpu_x264"
    assert plan.video_quality == 18


def test_cpu_x264_render_uses_crf_and_configured_preset(tmp_path) -> None:
    plan = _plan(tmp_path, encoder="cpu_x264", video_quality=20)
    settings = Settings(_env_file=None, ffmpeg_path=Path("ffmpeg"))

    argv = build_ffmpeg_clip_args(plan, settings, tmp_path / "out.mp4")

    assert "-c:v" in argv
    video_codec_index = argv.index("-c:v")
    assert argv[video_codec_index + 1] == "libx264"
    assert argv[argv.index("-crf") + 1] == "20"
    assert argv[argv.index("-preset") + 1] == "veryfast"
    assert "h264_nvenc" not in argv
    assert "-cq" not in argv


def test_gpu_nvenc_render_uses_constant_quality_vbr(tmp_path) -> None:
    plan = _plan(tmp_path, encoder="gpu_nvenc", video_quality=24)
    settings = Settings(_env_file=None, ffmpeg_path=Path("ffmpeg"))

    argv = build_ffmpeg_clip_args(plan, settings, tmp_path / "out.mp4")

    video_codec_index = argv.index("-c:v")
    assert argv[video_codec_index + 1] == "h264_nvenc"
    assert argv[argv.index("-rc") + 1] == "vbr"
    assert argv[argv.index("-cq") + 1] == "24"
    assert argv[argv.index("-b:v") + 1] == "0"
    # NVENC uses its own preset scale (p1-p7) — never the x264 preset name.
    assert "-crf" not in argv
    assert "libx264" not in argv
