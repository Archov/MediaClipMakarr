"""Background job API routes."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from mediaclipmakarr.jobs import (
    JobEventBroker,
    JobSnapshot,
    get_job_snapshot,
    job_sse_payload,
)


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/jobs/{job_id}", response_model=JobSnapshot)
    async def get_job(job_id: str, request: Request) -> JobSnapshot:
        snapshot = await get_job_snapshot(request.app.state.database_engine, job_id)
        if snapshot is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "JOB_NOT_FOUND",
                    "message": "The requested job does not exist.",
                    "retryable": False,
                },
            )
        return snapshot

    @router.get("/api/jobs/{job_id}/events")
    async def stream_job(job_id: str, request: Request) -> StreamingResponse:
        if await get_job_snapshot(request.app.state.database_engine, job_id) is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "JOB_NOT_FOUND",
                    "message": "The requested job does not exist.",
                    "retryable": False,
                },
            )

        async def events():
            broker: JobEventBroker = request.app.state.job_events
            version = broker.version(job_id)
            snapshot = await get_job_snapshot(request.app.state.database_engine, job_id)
            if snapshot is not None:
                yield job_sse_payload(snapshot)
            while not await request.is_disconnected():
                version, changed = await broker.wait_for_change(
                    job_id, version, timeout_seconds=15.0
                )
                if changed:
                    snapshot = broker.snapshot(job_id) or await get_job_snapshot(
                        request.app.state.database_engine, job_id
                    )
                    if snapshot is not None:
                        yield job_sse_payload(snapshot)
                else:
                    yield ": keep-alive\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
