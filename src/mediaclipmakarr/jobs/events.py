"""In-memory coordination for job SSE updates."""

import asyncio
import json

from .models import JobSnapshot


class JobEventBroker:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._versions: dict[str, int] = {}
        self._snapshots: dict[str, JobSnapshot] = {}

    def version(self, job_id: str) -> int:
        return self._versions.get(job_id, 0)

    def snapshot(self, job_id: str) -> JobSnapshot | None:
        return self._snapshots.get(job_id)

    async def publish(self, job_id: str, snapshot: JobSnapshot | None = None) -> None:
        async with self._condition:
            if snapshot is not None:
                self._snapshots[job_id] = snapshot
            self._versions[job_id] = self.version(job_id) + 1
            self._condition.notify_all()

    async def wait_for_change(
        self, job_id: str, version: int, *, timeout_seconds: float
    ) -> tuple[int, bool]:
        async with self._condition:
            if self.version(job_id) == version:
                try:
                    await asyncio.wait_for(
                        self._condition.wait_for(lambda: self.version(job_id) != version),
                        timeout_seconds,
                    )
                except TimeoutError:
                    return self.version(job_id), False
            return self.version(job_id), True


def job_sse_payload(snapshot: JobSnapshot) -> str:
    data = snapshot.model_dump(mode="json")
    return f"event: snapshot\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"

__all__ = ["JobEventBroker", "job_sse_payload"]
