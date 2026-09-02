from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import partial
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


class MediaProcessGate:
    """Keep FFmpeg mutations and live frame extraction sequential."""

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(1)

    @asynccontextmanager
    async def slot(self):
        async with self._semaphore:
            yield


class BlockingIOExecutor:
    """The only supported boundary for blocking filesystem and metadata work."""

    def __init__(self, max_workers: int) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="mcm-blocking-io",
        )
        self._closed = False

    async def run(self, function: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
        if self._closed:
            raise RuntimeError("The blocking-I/O executor is already closed.")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, partial(function, *args, **kwargs))

    async def shutdown(self) -> None:
        if not self._closed:
            self._closed = True
            await asyncio.to_thread(
                self._executor.shutdown,
                wait=True,
                cancel_futures=True,
            )
