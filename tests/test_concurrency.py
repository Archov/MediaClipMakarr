from __future__ import annotations

import asyncio
import threading

import pytest

from mediaclipmakarr.concurrency import BlockingIOExecutor


@pytest.mark.asyncio
async def test_blocking_work_runs_outside_event_loop_thread() -> None:
    executor = BlockingIOExecutor(max_workers=1)
    event_loop_thread = threading.get_ident()
    try:
        worker_thread = await executor.run(threading.get_ident)
    finally:
        await executor.shutdown()

    assert worker_thread != event_loop_thread


@pytest.mark.asyncio
async def test_executor_honors_its_worker_bound() -> None:
    executor = BlockingIOExecutor(max_workers=2)
    gate = threading.Barrier(2)

    def wait_for_peer() -> int:
        gate.wait(timeout=2)
        return threading.get_ident()

    try:
        thread_ids = await asyncio.gather(executor.run(wait_for_peer), executor.run(wait_for_peer))
    finally:
        await executor.shutdown()

    assert len(set(thread_ids)) == 2


@pytest.mark.asyncio
async def test_executor_shutdown_does_not_block_the_event_loop() -> None:
    executor = BlockingIOExecutor(max_workers=1)
    worker_started = threading.Event()
    release_worker = threading.Event()

    def wait_for_release() -> None:
        worker_started.set()
        release_worker.wait(timeout=2)

    work = asyncio.create_task(executor.run(wait_for_release))
    assert await asyncio.to_thread(worker_started.wait, 1)

    shutdown = asyncio.create_task(executor.shutdown())
    await asyncio.sleep(0)
    assert not shutdown.done()

    release_worker.set()
    await shutdown
    await work
