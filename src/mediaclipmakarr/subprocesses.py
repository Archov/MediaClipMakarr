from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class CommandError(RuntimeError):
    """Base error for an asynchronously executed child process."""


class CommandNotFoundError(CommandError):
    pass


class CommandLaunchError(CommandError):
    pass


class CommandTimeoutError(CommandError):
    pass


class CommandFailedError(CommandError):
    def __init__(self, executable: str, returncode: int, stderr: str) -> None:
        detail = stderr.strip() or "No error output was returned."
        super().__init__(f"{executable} exited with code {returncode}: {detail}")
        self.returncode = returncode


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


async def _read_bounded(stream: asyncio.StreamReader | None, limit: int) -> bytes:
    if stream is None:
        return b""
    chunks: list[bytes] = []
    retained = 0
    while chunk := await stream.read(64 * 1024):
        if retained < limit:
            kept = chunk[: limit - retained]
            chunks.append(kept)
            retained += len(kept)
    return b"".join(chunks)


async def run_command(
    argv: Sequence[str | os.PathLike[str]],
    *,
    timeout_seconds: float,
    check: bool = True,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    output_limit_bytes: int = 2 * 1024 * 1024,
) -> CommandResult:
    """Run an argument-array command without a shell and with bounded captured output."""

    normalized = tuple(os.fspath(argument) for argument in argv)
    if not normalized:
        raise ValueError("A subprocess command must include an executable.")

    try:
        process = await asyncio.create_subprocess_exec(
            *normalized,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        if isinstance(error, FileNotFoundError):
            raise CommandNotFoundError(
                f"Required executable '{normalized[0]}' was not found."
            ) from error
        raise CommandLaunchError(
            f"Required executable '{normalized[0]}' could not be started ({error.strerror})."
        ) from error

    stdout_task = asyncio.create_task(_read_bounded(process.stdout, output_limit_bytes))
    stderr_task = asyncio.create_task(_read_bounded(process.stderr, output_limit_bytes))
    try:
        stdout_bytes, stderr_bytes, returncode = await asyncio.wait_for(
            asyncio.gather(stdout_task, stderr_task, process.wait()),
            timeout=timeout_seconds,
        )
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise
    except TimeoutError as error:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise CommandTimeoutError(
            f"{normalized[0]} did not finish within {timeout_seconds:g} seconds."
        ) from error

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    result = CommandResult(normalized, returncode, stdout, stderr)
    if check and returncode != 0:
        raise CommandFailedError(normalized[0], returncode, stderr)
    return result
