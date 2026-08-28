from __future__ import annotations

import pytest

from mediaclipmakarr.config import Settings
from mediaclipmakarr.health import inspect_media_tools
from mediaclipmakarr.subprocesses import CommandResult


@pytest.mark.asyncio
async def test_media_tool_inspection_requires_identity_and_capabilities() -> None:
    async def runner(argv, **_kwargs):
        command = tuple(str(value) for value in argv)
        argument = command[-1]
        outputs = {
            "-version": "ffmpeg version 7.1.4-Jellyfin Copyright",
            "-encoders": " V..... libx264 H.264\n A..... aac AAC",
            "-filters": " ..C scale Scale the input video size",
            "-formats": "  E mp4 MP4 (MPEG-4 Part 14)",
        }
        output = outputs[argument]
        if "ffprobe" in command[0] and argument == "-version":
            output = "ffprobe version 7.1.4-Jellyfin Copyright"
        return CommandResult(command, 0, output, "")

    inspection = await inspect_media_tools(Settings(_env_file=None), runner=runner)

    assert inspection.status == "ok"
    assert inspection.details["libx264"] is True
    assert inspection.details["mp4_muxer"] is True


@pytest.mark.asyncio
async def test_media_tool_inspection_reports_missing_capability() -> None:
    async def runner(argv, **_kwargs):
        command = tuple(str(value) for value in argv)
        argument = command[-1]
        outputs = {
            "-version": "ffmpeg version 7.1.4-Jellyfin Copyright",
            "-encoders": " V..... libx264 H.264\n A..... aac AAC",
            "-filters": " ..C scale Scale the input video size",
            "-formats": " D  matroska Matroska",
        }
        output = outputs[argument]
        if "ffprobe" in command[0] and argument == "-version":
            output = "ffprobe version 7.1.4-Jellyfin Copyright"
        return CommandResult(command, 0, output, "")

    inspection = await inspect_media_tools(Settings(_env_file=None), runner=runner)

    assert inspection.status == "error"
    assert "mp4_muxer" in inspection.message
