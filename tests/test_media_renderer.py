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
    MediaCapabilities,
    MediaStreamIdentity,
    ResolvedSourceMedia,
    SourceFingerprint,
    VideoColorMetadata,
    VideoStreamIdentity,
)


def _plan(
    tmp_path: Path,
    *,
    decode: str = "cpu",
    tonemap: str = "cpu",
    encoder: str = "cpu_x264",
    video_quality: int = 18,
    max_resolution: str = "1080p",
    max_fps: int = 60,
    frame_rate: float | None = None,
):
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
        capabilities=MediaCapabilities(
            duration_ms=10_000,
            frame_rate=frame_rate,
            video_tracks=[],
            audio_tracks=[],
            subtitle_tracks=[],
            attachment_tracks=[],
            default_audio_stream_index=1,
            hdr=HdrCapabilities(),
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
        decode=decode,
        tonemap=tonemap,
        encoder=encoder,
        video_quality=video_quality,
        max_resolution=max_resolution,
        max_fps=max_fps,
    )


def test_build_clip_render_plan_defaults_to_cpu_x264(tmp_path) -> None:
    plan = _plan(tmp_path)

    assert plan.encoder == "cpu_x264"
    assert plan.video_quality == 18
    assert plan.max_resolution == "1080p"
    assert plan.max_fps == 60


def test_max_resolution_flows_through_to_the_scale_filter(tmp_path) -> None:
    plan = _plan(tmp_path, max_resolution="720p")
    settings = Settings(_env_file=None, ffmpeg_path=Path("ffmpeg"))

    argv = build_ffmpeg_clip_args(plan, settings, tmp_path / "out.mp4")

    video_filter = argv[argv.index("-vf") + 1]
    assert "min(1280,iw)" in video_filter
    assert "min(720,ih)" in video_filter


def test_max_fps_is_omitted_when_source_frame_rate_is_at_or_under_it(tmp_path) -> None:
    plan = _plan(tmp_path, max_fps=30, frame_rate=23.976)
    settings = Settings(_env_file=None, ffmpeg_path=Path("ffmpeg"))

    argv = build_ffmpeg_clip_args(plan, settings, tmp_path / "out.mp4")

    assert "fps=" not in argv[argv.index("-vf") + 1]


def test_max_fps_applies_only_when_source_frame_rate_exceeds_it(tmp_path) -> None:
    plan = _plan(tmp_path, max_fps=30, frame_rate=59.94)
    settings = Settings(_env_file=None, ffmpeg_path=Path("ffmpeg"))

    argv = build_ffmpeg_clip_args(plan, settings, tmp_path / "out.mp4")

    assert "fps=30," in argv[argv.index("-vf") + 1]


def test_max_fps_is_omitted_when_source_frame_rate_is_unknown(tmp_path) -> None:
    plan = _plan(tmp_path, max_fps=30, frame_rate=None)
    settings = Settings(_env_file=None, ffmpeg_path=Path("ffmpeg"))

    argv = build_ffmpeg_clip_args(plan, settings, tmp_path / "out.mp4")

    assert "fps=" not in argv[argv.index("-vf") + 1]


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


def _hdr_plan(tmp_path: Path, **overrides) -> object:
    plan = _plan(tmp_path, **overrides)
    hdr = HdrCapabilities(
        hlg=True,
        color=VideoColorMetadata(
            color_space="bt2020nc",
            color_transfer="arib-std-b67",
            color_primaries="bt2020",
            color_range="tv",
        ),
    )
    return plan.model_copy(update={"hdr": hdr, "hdr_strategy": "tone_map_hlg"})


def test_gpu_decode_requests_hardware_decode_independent_of_encoder(tmp_path) -> None:
    """Decode, tonemap, and encode are three independent settings — GPU
    decode with CPU encode must still request NVDEC, placed as an input
    option (before -i), or ffmpeg ignores it."""
    plan = _plan(tmp_path, decode="gpu", encoder="cpu_x264")
    settings = Settings(_env_file=None, ffmpeg_path=Path("ffmpeg"))

    argv = build_ffmpeg_clip_args(plan, settings, tmp_path / "out.mp4")

    assert "-hwaccel" in argv
    assert argv[argv.index("-hwaccel") + 1] == "cuda"
    assert argv.index("-hwaccel") < argv.index("-i")
    # Not paired with -hwaccel_output_format cuda: frames must land back in
    # ordinary system memory so the existing CPU-side filter chain (scale,
    # subtitle burn-in, HDR tonemap) keeps working unchanged.
    assert "-hwaccel_output_format" not in argv
    assert argv[argv.index("-c:v") + 1] == "libx264"


def test_gpu_encoder_alone_does_not_imply_gpu_decode(tmp_path) -> None:
    """The inverse of the above: GPU encode with CPU decode (the settings'
    defaults) must not request any hwaccel."""
    plan = _plan(tmp_path, decode="cpu", encoder="gpu_nvenc")
    settings = Settings(_env_file=None, ffmpeg_path=Path("ffmpeg"))

    argv = build_ffmpeg_clip_args(plan, settings, tmp_path / "out.mp4")

    assert "-hwaccel" not in argv
    assert argv[argv.index("-c:v") + 1] == "h264_nvenc"


def test_hdr_tonemap_gpu_with_gpu_decode_uses_libplacebo_vulkan_decode(
    tmp_path,
) -> None:
    """HDR content with GPU decode+tonemap must route through libplacebo/
    Vulkan, not NVDEC+CPU-tonemap — tonemap_cuda produces badly underexposed
    output on real HLG content regardless of parameters (verified against a
    real broadcast file)."""
    plan = _hdr_plan(tmp_path, decode="gpu", tonemap="gpu", encoder="cpu_x264")
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


def test_hdr_tonemap_gpu_with_cpu_decode_skips_decode_hwaccel(tmp_path) -> None:
    """GPU tonemap with CPU decode: libplacebo uploads plain system-memory
    frames to its own Vulkan device internally (verified working), so only
    the device context is requested, not a decode hwaccel."""
    plan = _hdr_plan(tmp_path, decode="cpu", tonemap="gpu")
    settings = Settings(_env_file=None, ffmpeg_path=Path("ffmpeg"))

    argv = build_ffmpeg_clip_args(plan, settings, tmp_path / "out.mp4")

    assert argv[argv.index("-init_hw_device") + 1] == "vulkan=vk:0"
    assert argv[argv.index("-filter_hw_device") + 1] == "vk"
    assert "-hwaccel" not in argv
    assert "libplacebo=" in argv[argv.index("-vf") + 1]


def test_hdr_tonemap_cpu_still_uses_nvdec_when_decode_is_gpu(tmp_path) -> None:
    """CPU tonemap (tonemapx) with GPU decode: NVDEC decode (cuda hwaccel,
    frames land in system memory) feeds the CPU tonemap filter directly, no
    Vulkan device needed."""
    plan = _hdr_plan(tmp_path, decode="gpu", tonemap="cpu")
    settings = Settings(_env_file=None, ffmpeg_path=Path("ffmpeg"))

    argv = build_ffmpeg_clip_args(plan, settings, tmp_path / "out.mp4")

    assert argv[argv.index("-hwaccel") + 1] == "cuda"
    assert "-init_hw_device" not in argv
    assert "-hwaccel_output_format" not in argv
    video_filter = argv[argv.index("-vf") + 1]
    assert "tonemapx=" in video_filter
    assert "libplacebo=" not in video_filter


def test_hdr_render_can_use_gpu_encode_when_explicitly_selected(tmp_path) -> None:
    """Encode is independent of decode/tonemap — HDR content with GPU encode
    explicitly selected must actually use NVENC now (earlier behavior forced
    libx264 for all HDR renders; the three-way split lets the user opt back
    into NVENC if they want it despite the real bitrate cost measured on
    grainy/high-motion HDR content)."""
    plan = _hdr_plan(tmp_path, decode="gpu", tonemap="gpu", encoder="gpu_nvenc")
    settings = Settings(_env_file=None, ffmpeg_path=Path("ffmpeg"))

    argv = build_ffmpeg_clip_args(plan, settings, tmp_path / "out.mp4")

    assert argv[argv.index("-c:v") + 1] == "h264_nvenc"
    assert argv[argv.index("-rc") + 1] == "constqp"
