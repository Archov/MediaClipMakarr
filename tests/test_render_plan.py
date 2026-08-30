from __future__ import annotations

from datetime import UTC, datetime

from mediaclipmakarr.clips import ClipCreateRequest
from mediaclipmakarr.plex import PlexSession
from mediaclipmakarr.render_plan import ClipRenderPlan, build_clip_render_plan
from mediaclipmakarr.source_media import (
    MediaStreamIdentity,
    ResolvedSourceMedia,
    SourceFingerprint,
    VideoColorMetadata,
    VideoStreamIdentity,
)


def test_legacy_render_plan_subtitle_keys_are_ignored_without_rehashing(tmp_path) -> None:
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
    plan = build_clip_render_plan(
        session=session,
        request=ClipCreateRequest(
            session_identity=session.session_identity,
            media_identity=session.media_identity,
            start_ms=1_000,
            end_ms=4_000,
        ),
        source_media=source_media,
        x264_preset="veryfast",
    )
    legacy_payload = plan.model_dump(mode="json")
    legacy_payload.pop("selected_subtitle")
    legacy_payload["source_media"].pop("attachment_streams")
    legacy_payload["source_media"].pop("capabilities")
    legacy_payload["source_media"].pop("selected_subtitle")
    legacy_payload["subtitle_stream_index"] = 3
    legacy_payload["selected_subtitle_stream_index"] = 3
    legacy_payload["render_plan_hash"] = "persisted-legacy-hash"

    restored = ClipRenderPlan.model_validate(legacy_payload)

    assert restored.selected_subtitle.enabled is False
    assert restored.render_plan_hash == "persisted-legacy-hash"
