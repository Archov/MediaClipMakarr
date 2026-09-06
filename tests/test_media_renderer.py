from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mediaclipmakarr.clips import ClipCreateRequest
from mediaclipmakarr.config import Settings
from mediaclipmakarr.hdr import HdrCapabilities
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
    # CPU rendering never requests hardware decode — there's no GPU to assume.
    assert "-hwaccel" not in argv


def test_gpu_nvenc_render_uses_constant_qp(tmp_path) -> None:
    plan = _plan(tmp_path, encoder="gpu_nvenc", video_quality=24)
    settings = Settings(_env_file=None, ffmpeg_path=Path("ffmpeg"))

    argv = build_ffmpeg_clip_args(plan, settings, tmp_path / "out.mp4")

    video_codec_index = argv.index("-c:v")
    assert argv[video_codec_index + 1] == "h264_nvenc"
    # constqp, not vbr+cq: vbr+cq+tune=hq lets NVENC spend far more bitrate
    # than the quality number implies (measured ~3.7x an equivalent x264
    # CRF on real content) since nothing bounds how low it can push the QP.
    assert argv[argv.index("-rc") + 1] == "constqp"
    assert argv[argv.index("-qp") + 1] == "24"
    # NVENC uses its own preset scale (p1-p7) — never the x264 preset name.
    assert "-crf" not in argv
    assert "-cq" not in argv
    assert "libx264" not in argv


def test_gpu_nvenc_render_also_requests_hardware_decode(tmp_path) -> None:
    """Encode-only GPU acceleration leaves the actual bottleneck (decoding a
    4K/HEVC source) on the CPU — hwaccel cuda must be requested too, and
    placed as an input option (before -i), or ffmpeg ignores it."""
    plan = _plan(tmp_path, encoder="gpu_nvenc")
    settings = Settings(_env_file=None, ffmpeg_path=Path("ffmpeg"))

    argv = build_ffmpeg_clip_args(plan, settings, tmp_path / "out.mp4")

    assert "-hwaccel" in argv
    assert argv[argv.index("-hwaccel") + 1] == "cuda"
    assert argv.index("-hwaccel") < argv.index("-i")
    # Not paired with -hwaccel_output_format cuda: frames must land back in
    # ordinary system memory so the existing CPU-side filter chain (scale,
    # subtitle burn-in, HDR tonemap) keeps working unchanged.
    assert "-hwaccel_output_format" not in argv


def test_gpu_nvenc_hdr_render_uses_libplacebo_vulkan_decode_and_cpu_encode(
    tmp_path,
) -> None:
    """HDR content on the GPU encoder must route decode+tonemap through
    libplacebo/Vulkan, not NVDEC+CPU-tonemap — tonemap_cuda produces badly
    underexposed output on real HLG content regardless of parameters
    (verified against a real broadcast file). The *encode* step, though,
    stays on libx264 even here: NVENC's efficiency gap vs x264 on this
    (Pascal-generation) hardware widens to ~1.7x on real grainy/high-motion
    HDR broadcast footage (vs ~1.2x on clean SDR content) — decode/tonemap
    was the actual bottleneck, not encode, so there's nothing to gain from
    NVENC here and a real file-size cost to keeping it."""
    plan = _plan(tmp_path, encoder="gpu_nvenc")
    hdr = HdrCapabilities(
        hlg=True,
        color=VideoColorMetadata(
            color_space="bt2020nc",
            color_transfer="arib-std-b67",
            color_primaries="bt2020",
            color_range="tv",
        ),
    )
    plan = plan.model_copy(update={"hdr": hdr, "hdr_strategy": "tone_map_hlg"})
    settings = Settings(_env_file=None, ffmpeg_path=Path("ffmpeg"))

    argv = build_ffmpeg_clip_args(plan, settings, tmp_path / "out.mp4")

    assert argv[argv.index("-init_hw_device") + 1] == "vulkan=vk:0"
    assert argv[argv.index("-filter_hw_device") + 1] == "vk"
    assert argv[argv.index("-hwaccel") + 1] == "vulkan"
    assert argv[argv.index("-hwaccel_output_format") + 1] == "vulkan"
    video_filter = argv[argv.index("-vf") + 1]
    assert "libplacebo=" in video_filter
    assert "tonemapping=mobius" in video_filter
    assert "tonemapx=" not in video_filter
    assert argv[argv.index("-c:v") + 1] == "libx264"
    assert argv[argv.index("-crf") + 1] == "18"
    assert "h264_nvenc" not in argv
