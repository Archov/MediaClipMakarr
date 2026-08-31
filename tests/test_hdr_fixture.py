from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mediaclipmakarr.clips import ClipCreateRequest
from mediaclipmakarr.config import Settings
from mediaclipmakarr.hdr import HdrCapabilities, VideoColorMetadata
from mediaclipmakarr.media_renderer import build_ffmpeg_clip_args
from mediaclipmakarr.plex import PlexSession
from mediaclipmakarr.render_plan import build_clip_render_plan
from mediaclipmakarr.source_media import (
    MediaStreamIdentity,
    ResolvedSourceMedia,
    SourceFingerprint,
    VideoStreamIdentity,
)
from mediaclipmakarr.subprocesses import run_command


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fixture_name", "transfer", "strategy"),
    [
        ("hdr10-sanity.mkv", "smpte2084", "tone_map_hdr10"),
        ("hlg-sanity.mkv", "arib-std-b67", "tone_map_hlg"),
    ],
)
async def test_hdr_fixture_renders_as_sane_limited_bt709_frame(
    tmp_path, fixture_name: str, transfer: str, strategy: str
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg and ffprobe are required for the HDR media smoke test.")
    fixture = Path(__file__).parent / "fixtures" / fixture_name
    stat = fixture.stat()
    source = ResolvedSourceMedia(
        plex_path=f"/plex/{fixture_name}",
        local_path=str(fixture),
        fingerprint=SourceFingerprint(
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
        ),
        duration_ms=1_000,
        video_streams=[
            VideoStreamIdentity(
                stream_index=0,
                codec_type="video",
                codec_name="hevc",
                width=128,
                height=72,
                color=VideoColorMetadata(
                    color_space="bt2020nc",
                    color_transfer=transfer,
                    color_primaries="bt2020",
                    color_range="tv",
                ),
            )
        ],
        audio_streams=[MediaStreamIdentity(stream_index=1, codec_type="audio", codec_name="aac")],
        subtitle_streams=[],
        selected_audio_stream=MediaStreamIdentity(
            stream_index=1, codec_type="audio", codec_name="aac"
        ),
    )
    session = PlexSession(
        session_identity="fixture-session",
        media_identity="fixture-media",
        title="HDR fixture",
        media_type="movie",
        plex_user=None,
        player=None,
        state="playing",
        position_ms=0,
        duration_ms=1_000,
        sampled_at=datetime.now(UTC),
    )
    plan = build_clip_render_plan(
        session=session,
        request=ClipCreateRequest(
            session_identity=session.session_identity,
            media_identity=session.media_identity,
            start_ms=0,
            end_ms=800,
        ),
        source_media=source,
        x264_preset="ultrafast",
    ).model_copy(
        update={
            "hdr": HdrCapabilities(
                hdr10=strategy == "tone_map_hdr10",
                hlg=strategy == "tone_map_hlg",
                color=source.video_streams[0].color,
            ),
            "hdr_strategy": strategy,
        }
    )
    output = tmp_path / "rendered.mp4"
    argv = build_ffmpeg_clip_args(
        plan,
        Settings(_env_file=None, ffmpeg_path=Path(ffmpeg)),
        output,
    )

    await run_command(argv, timeout_seconds=30)
    probe = await run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,pix_fmt,color_space,color_transfer,color_primaries,color_range:"
            "format_tags=comment",
            "-of",
            "json",
            output,
        ],
        timeout_seconds=10,
    )
    probe_payload = json.loads(probe.stdout)
    [video] = probe_payload["streams"]
    assert video == {
        "width": 128,
        "height": 72,
        "pix_fmt": "yuv420p",
        "color_range": "tv",
        "color_space": "bt709",
        "color_transfer": "bt709",
        "color_primaries": "bt709",
    }
    comment = probe_payload["format"]["tags"]["comment"]
    recovery_metadata = json.loads(comment.removeprefix("MediaClipMakarr "))
    assert recovery_metadata["videoProcessing"]["hdrStrategy"] == strategy
    assert recovery_metadata["videoProcessing"]["sourceColor"]["color_transfer"] == transfer

    signal = await run_command(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            output,
            "-vf",
            "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        timeout_seconds=10,
    )
    match = re.search(r"lavfi\.signalstats\.YAVG=([0-9.]+)", signal.stderr)
    assert match is not None
    assert 16 < float(match.group(1)) < 235
