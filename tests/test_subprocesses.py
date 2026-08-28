from __future__ import annotations

import asyncio

import pytest

from mediaclipmakarr.subprocesses import run_command


class _WaitingProcess:
    def __init__(self) -> None:
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.wait_started = asyncio.Event()
        self.exited = asyncio.Event()
        self.killed = False
        self.wait_calls = 0

    async def wait(self) -> int:
        self.wait_calls += 1
        self.wait_started.set()
        await self.exited.wait()
        return -9

    def kill(self) -> None:
        self.killed = True
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.exited.set()


@pytest.mark.asyncio
async def test_run_command_kills_and_reaps_child_when_cancelled(monkeypatch) -> None:
    process = _WaitingProcess()

    async def create_process(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    command = asyncio.create_task(run_command(["fake-command"], timeout_seconds=10))
    await process.wait_started.wait()

    command.cancel()
    with pytest.raises(asyncio.CancelledError):
        await command

    assert process.killed is True
    assert process.wait_calls == 2
