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
        executor.shutdown()

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
        executor.shutdown()

    assert len(set(thread_ids)) == 2
