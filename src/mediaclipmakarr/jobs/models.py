"""Pure job-domain models and transition errors."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from mediaclipmakarr.render_plan import ClipRenderPlan

JobState = Literal["QUEUED", "RUNNING", "FINALIZING", "SUCCEEDED", "PARTIAL", "FAILED"]
JobType = Literal["clip_create"]
JobStage = Literal["queued", "validating", "rendering", "finalizing", "complete", "failed"]
BlockingRunner = Callable[..., Awaitable[Any]]


class JobError(BaseModel):
    code: str
    message: str
    retryable: bool = False
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class JobSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: JobType
    state: JobState
    stage: JobStage
    progress: float
    current_stage_progress: float
    elapsed_ms: int | None
    queue_position: int | None
    message: str
    result: dict[str, Any] | None = None
    error: JobError | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: str
    run_token: str
    render_plan: ClipRenderPlan


class JobUpdateConflict(RuntimeError):
    pass


__all__ = [
    "BlockingRunner",
    "ClaimedJob",
    "JobError",
    "JobSnapshot",
    "JobStage",
    "JobUpdateConflict",
]
