from __future__ import annotations

import json
from pathlib import Path

import pytest

from mediaclipmakarr.application_settings import EffectiveApplicationSettings
from mediaclipmakarr.config import Settings
from mediaclipmakarr.plex import PlexPartStream, PlexSession
from mediaclipmakarr.source_media import (
    SourceMediaError,
    resolve_and_probe_source_media,
    resolve_media_capabilities,
)
from mediaclipmakarr.source_paths import SourcePathMapping
from mediaclipmakarr.subprocesses import CommandResult


def effective_settings(source_root: Path) -> EffectiveApplicationSettings:
    return EffectiveApplicationSettings(
        plex_url="http://plex.example:32400",
        plex_token="token",
        source_path_mappings=[
            SourcePathMapping(plex_prefix="/plex", local_prefix=str(source_root))
        ],
        timezone="UTC",
        timezone_configured=True,
        x264_preset="veryfast",
        environment_managed={
            "plex_url": False,
            "plex_token": False,
            "source_path_mappings": False,
            "timezone": False,
            "x264_preset": False,
        },
    )


def session(
    *,
    plex_part_file: str = "/plex/Movie.mkv",
    selected_audio_streams: list[PlexPartStream] | None = None,
) -> PlexSession:
    return PlexSession(
        session_identity="plex-session:living-room",
        media_identity="plex-media:movie",
        title="A Movie",
        media_type="movie",
        plex_user="Alice",
        player="Living Room",
        state="playing",
        position_ms=10_000,
        duration_ms=90_000,
        sampled_at="2026-08-28T12:00:00Z",
        plex_rating_key="501",
        plex_media_key="media-501",
        plex_part_id="part-501",
        plex_part_file=plex_part_file,
        selected_audio_streams=selected_audio_streams
        if selected_audio_streams is not None
        else [PlexPartStream(stream_type=2, stream_index=1, selected=True)],
    )


async def run_blocking(function, *args):
    return function(*args)


def probe_payload(*, color_transfer: str = "bt709", audio_indexes=(1,)) -> str:
    return json.dumps(
        {
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "color_space": "bt709",
                    "color_transfer": color_transfer,
                    "color_primaries": "bt709",
                    "color_range": "tv",
                },
                *[
                    {
                        "index": index,
                        "codec_type": "audio",
                        "codec_name": "aac",
                        "tags": {"language": "eng", "title": f"Audio {index}"},
                    }
                    for index in audio_indexes
                ],
                {
                    "index": 3,
                    "codec_type": "subtitle",
                    "codec_name": "subrip",
                    "tags": {"language": "eng"},
                },
                {
                    "index": 4,
                    "codec_type": "subtitle",
                    "codec_name": "hdmv_pgs_subtitle",
                    "tags": {"language": "jpn"},
                },
            ],
            "format": {"duration": "12.345"},
        }
    )


@pytest.mark.asyncio
async def test_resolve_and_probe_captures_fingerprint_duration_and_selected_audio(
    tmp_path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    media = source_root / "Movie.mkv"
    media.write_bytes(b"fake media")
    observed_argv: tuple[str, ...] | None = None

    async def runner(argv, **kwargs):
        nonlocal observed_argv
        observed_argv = tuple(str(value) for value in argv)
        assert kwargs["timeout_seconds"] == 3
        return CommandResult(observed_argv, 0, probe_payload(), "")

    result = await resolve_and_probe_source_media(
        session(),
        effective_settings(source_root),
        Settings(
            _env_file=None,
            source_dirs=[source_root],
            ffprobe_path=Path("test-ffprobe"),
            subprocess_timeout_seconds=3,
        ),
        run_blocking=run_blocking,
        runner=runner,
    )

    assert observed_argv == (
        "test-ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(media.resolve()),
    )
    assert result.local_path == str(media.resolve())
    assert result.fingerprint.size_bytes == len(b"fake media")
    assert result.duration_ms == 12_345
    assert result.video_streams[0].color.color_transfer == "bt709"
    assert result.selected_audio_stream.stream_index == 1
    assert result.capabilities is not None
    assert result.capabilities.audio_tracks[0].selected is True
    assert result.subtitle_streams[0].codec_name == "subrip"
    assert result.subtitles_forced_off is True


@pytest.mark.asyncio
async def test_probe_preserves_attachment_filename_and_mime_type(tmp_path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "Movie.mkv").write_bytes(b"fake media")
    payload = json.loads(probe_payload())
    payload["streams"].append(
        {
            "index": 5,
            "codec_type": "attachment",
            "tags": {
                "filename": "Cabin-Bold.otf",
                "mimetype": "application/vnd.ms-opentype",
            },
        }
    )

    async def runner(argv, **_kwargs):
        return CommandResult(tuple(str(value) for value in argv), 0, json.dumps(payload), "")

    result = await resolve_and_probe_source_media(
        session(),
        effective_settings(source_root),
        Settings(_env_file=None, source_dirs=[source_root]),
        run_blocking=run_blocking,
        runner=runner,
    )

    attachment = result.attachment_streams[0]
    assert attachment.filename == "Cabin-Bold.otf"
    assert attachment.mime_type == "application/vnd.ms-opentype"


@pytest.mark.asyncio
async def test_unmapped_or_missing_paths_do_not_reach_ffprobe(tmp_path) -> None:
    calls = 0

    async def runner(argv, **kwargs):
        nonlocal calls
        calls += 1
        return CommandResult(tuple(str(value) for value in argv), 0, probe_payload(), "")

    source_root = tmp_path / "source"
    source_root.mkdir()
    settings = effective_settings(source_root)
    bootstrap = Settings(_env_file=None, source_dirs=[source_root])

    with pytest.raises(SourceMediaError) as unmapped:
        await resolve_and_probe_source_media(
            session(plex_part_file="/other/Movie.mkv"),
            settings,
            bootstrap,
            run_blocking=run_blocking,
            runner=runner,
        )
    with pytest.raises(SourceMediaError) as missing:
        await resolve_and_probe_source_media(
            session(),
            settings,
            bootstrap,
            run_blocking=run_blocking,
            runner=runner,
        )

    assert unmapped.value.code == "SOURCE_PATH_UNMAPPED"
    assert missing.value.code == "SOURCE_PATH_MISSING"
    assert calls == 0


@pytest.mark.asyncio
async def test_selected_audio_must_map_unambiguously(tmp_path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "Movie.mkv").write_bytes(b"fake media")

    async def runner(argv, **_kwargs):
        return CommandResult(
            tuple(str(value) for value in argv),
            0,
            probe_payload(audio_indexes=(1, 2)),
            "",
        )

    with pytest.raises(SourceMediaError) as unavailable:
        await resolve_and_probe_source_media(
            session(
                selected_audio_streams=[
                    PlexPartStream(stream_type=2, stream_index=9, selected=True)
                ]
            ),
            effective_settings(source_root),
            Settings(_env_file=None, source_dirs=[source_root]),
            run_blocking=run_blocking,
            runner=runner,
        )
    with pytest.raises(SourceMediaError) as ambiguous:
        await resolve_and_probe_source_media(
            session(selected_audio_streams=[PlexPartStream(stream_type=2, selected=True)]),
            effective_settings(source_root),
            Settings(_env_file=None, source_dirs=[source_root]),
            run_blocking=run_blocking,
            runner=runner,
        )

    assert unavailable.value.code == "AUDIO_STREAM_UNAVAILABLE"
    assert ambiguous.value.code == "AUDIO_STREAM_AMBIGUOUS"


@pytest.mark.asyncio
@pytest.mark.parametrize("transfer", ["smpte2084", "arib-std-b67"])
async def test_hdr_sources_are_classified_for_tone_mapping(tmp_path, transfer) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "Movie.mkv").write_bytes(b"fake media")

    async def runner(argv, **_kwargs):
        return CommandResult(
            tuple(str(value) for value in argv),
            0,
            probe_payload(color_transfer=transfer),
            "",
        )

    result = await resolve_and_probe_source_media(
        session(),
        effective_settings(source_root),
        Settings(_env_file=None, source_dirs=[source_root]),
        run_blocking=run_blocking,
        runner=runner,
    )

    assert result.capabilities is not None
    assert result.capabilities.hdr.hdr10 is (transfer == "smpte2084")
    assert result.capabilities.hdr.hlg is (transfer == "arib-std-b67")


@pytest.mark.asyncio
async def test_dolby_vision_detection_uses_authoritative_ffprobe_metadata_only(tmp_path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "Movie.mkv").write_bytes(b"fake media")
    payload = json.loads(probe_payload())
    payload["streams"][0]["tags"] = {
        "title": "Dovi is a character name",
        "encoder": "dovi-test-encoder",
    }

    async def runner(argv, **_kwargs):
        return CommandResult(tuple(str(value) for value in argv), 0, json.dumps(payload), "")

    result = await resolve_and_probe_source_media(
        session(),
        effective_settings(source_root),
        Settings(_env_file=None, source_dirs=[source_root]),
        run_blocking=run_blocking,
        runner=runner,
    )

    assert result.capabilities is not None
    assert result.capabilities.hdr.dolby_vision is False


@pytest.mark.asyncio
async def test_dolby_vision_configuration_record_is_classified_conservatively(tmp_path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "Movie.mkv").write_bytes(b"fake media")
    payload = json.loads(probe_payload())
    payload["streams"][0]["side_data_list"] = [
        {"side_data_type": "DOVI configuration record", "dv_profile": 8}
    ]

    async def runner(argv, **_kwargs):
        return CommandResult(tuple(str(value) for value in argv), 0, json.dumps(payload), "")

    result = await resolve_and_probe_source_media(
        session(),
        effective_settings(source_root),
        Settings(_env_file=None, source_dirs=[source_root]),
        run_blocking=run_blocking,
        runner=runner,
    )

    assert result.capabilities is not None
    assert result.capabilities.hdr.dolby_vision is True
    assert result.capabilities.hdr.dolby_vision_profile == 8
    assert result.capabilities.hdr.dolby_vision_base_layer_compatible is None


@pytest.mark.asyncio
async def test_requested_audio_and_subtitle_tracks_are_selected_explicitly(tmp_path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "Movie.mkv").write_bytes(b"fake media")

    async def runner(argv, **_kwargs):
        return CommandResult(
            tuple(str(value) for value in argv),
            0,
            probe_payload(audio_indexes=(1, 2)),
            "",
        )

    result = await resolve_and_probe_source_media(
        session(),
        effective_settings(source_root),
        Settings(_env_file=None, source_dirs=[source_root]),
        run_blocking=run_blocking,
        runner=runner,
        requested_audio_stream_index=2,
        requested_subtitle_stream_index=3,
        subtitles_enabled=True,
    )

    assert result.selected_audio_stream.stream_index == 2
    assert result.selected_subtitle.enabled is True
    assert result.selected_subtitle.strategy == "embedded_text"
    assert result.selected_subtitle.stream is not None
    assert result.selected_subtitle.stream.stream_index == 3


@pytest.mark.asyncio
async def test_bitmap_subtitle_track_selects_bitmap_strategy(tmp_path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "Movie.mkv").write_bytes(b"fake media")

    async def runner(argv, **_kwargs):
        return CommandResult(tuple(str(value) for value in argv), 0, probe_payload(), "")

    result = await resolve_and_probe_source_media(
        session(),
        effective_settings(source_root),
        Settings(_env_file=None, source_dirs=[source_root]),
        run_blocking=run_blocking,
        runner=runner,
        requested_subtitle_stream_index=4,
        subtitles_enabled=True,
    )

    assert result.selected_subtitle.strategy == "bitmap"


@pytest.mark.asyncio
async def test_external_text_subtitle_uses_the_plex_stream_download_path(tmp_path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "Movie.mkv").write_bytes(b"fake media")
    external = PlexPartStream(
        id="external-subtitle",
        key="/library/streams/501.srt",
        stream_index=-1,
        stream_type=3,
        codec="srt",
        language="eng",
        selected=True,
    )
    selected_session = session().model_copy(
        update={
            "selected_subtitle_streams": [external],
            "subtitle_streams": [external],
        }
    )

    async def runner(argv, **_kwargs):
        return CommandResult(tuple(str(value) for value in argv), 0, probe_payload(), "")

    result = await resolve_and_probe_source_media(
        selected_session,
        effective_settings(source_root),
        Settings(_env_file=None, source_dirs=[source_root]),
        run_blocking=run_blocking,
        runner=runner,
        requested_subtitle_stream_index=-1,
        subtitles_enabled=True,
    )

    assert result.selected_subtitle.strategy == "external_text"
    assert (
        result.selected_subtitle.external_url == "http://plex.example:32400/library/streams/501.srt"
    )
    assert result.capabilities is not None
    assert result.capabilities.subtitle_tracks[-1].external is True
    assert result.capabilities.subtitle_tracks[-1].selected is True


@pytest.mark.asyncio
async def test_plex_external_srt_without_stream_index_is_selectable(tmp_path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "Movie.mkv").write_bytes(b"fake media")
    external = PlexPartStream(
        id="plex-downloaded-subtitle",
        key="/library/streams/501.srt",
        stream_type=3,
        codec="srt",
        language="eng",
        title="Plex downloaded English",
        selected=True,
    )
    selected_session = session().model_copy(
        update={
            "selected_subtitle_streams": [external],
            "subtitle_streams": [external],
        }
    )

    async def runner(argv, **_kwargs):
        return CommandResult(tuple(str(value) for value in argv), 0, probe_payload(), "")

    capabilities_result = await resolve_media_capabilities(
        selected_session,
        effective_settings(source_root),
        Settings(_env_file=None, source_dirs=[source_root]),
        run_blocking=run_blocking,
        runner=runner,
    )

    assert capabilities_result.capabilities is not None
    external_track = capabilities_result.capabilities.subtitle_tracks[-1]
    assert external_track.plex_track_id == "plex-downloaded-subtitle"
    assert external_track.stream_index == -1
    assert external_track.available is True
    assert external_track.selected is True
    assert capabilities_result.capabilities.default_subtitle_stream_index == -1
    assert capabilities_result.capabilities.subtitles_forced_off is False

    selected_result = await resolve_and_probe_source_media(
        selected_session,
        effective_settings(source_root),
        Settings(_env_file=None, source_dirs=[source_root]),
        run_blocking=run_blocking,
        runner=runner,
        requested_subtitle_stream_index=-1,
        subtitles_enabled=True,
    )

    assert selected_result.selected_subtitle.strategy == "external_text"
    assert selected_result.selected_subtitle.stream is not None
    assert selected_result.selected_subtitle.stream.stream_index == -1
    assert (
        selected_result.selected_subtitle.external_url
        == "http://plex.example:32400/library/streams/501.srt"
    )


@pytest.mark.asyncio
async def test_unsupported_subtitle_selection_returns_alternatives(tmp_path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "Movie.mkv").write_bytes(b"fake media")
    payload = json.dumps(
        {
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "h264"},
                {"index": 1, "codec_type": "audio", "codec_name": "aac"},
                {"index": 2, "codec_type": "subtitle", "codec_name": "unknown_subtitle"},
            ],
            "format": {"duration": "12.345"},
        }
    )

    async def runner(argv, **_kwargs):
        return CommandResult(tuple(str(value) for value in argv), 0, payload, "")

    with pytest.raises(SourceMediaError) as error:
        await resolve_and_probe_source_media(
            session(),
            effective_settings(source_root),
            Settings(_env_file=None, source_dirs=[source_root]),
            run_blocking=run_blocking,
            runner=runner,
            requested_subtitle_stream_index=2,
            subtitles_enabled=True,
        )

    assert error.value.code == "SUBTITLE_STREAM_UNSUPPORTED"
    assert error.value.alternatives == []


@pytest.mark.asyncio
async def test_media_capabilities_report_unavailable_subtitle_warnings(tmp_path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "Movie.mkv").write_bytes(b"fake media")
    payload = json.loads(probe_payload())
    payload["streams"].append(
        {"index": 9, "codec_type": "subtitle", "codec_name": "unknown_subtitle"}
    )

    async def runner(argv, **_kwargs):
        return CommandResult(tuple(str(value) for value in argv), 0, json.dumps(payload), "")

    result = await resolve_media_capabilities(
        session(),
        effective_settings(source_root),
        Settings(_env_file=None, source_dirs=[source_root]),
        run_blocking=run_blocking,
        runner=runner,
    )

    assert result.capabilities is not None
    assert result.capabilities.warnings == ["This subtitle codec cannot be burned yet."]
