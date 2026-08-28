from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mediaclipmakarr.plex import PlexSession, PlexSessionSnapshot

StructuredErrorCode = Literal[
    "CLIP_RANGE_NEGATIVE",
    "CLIP_RANGE_ORDER",
    "CLIP_RANGE_DURATION_EXCEEDED",
    "PLEX_SESSION_NOT_FOUND",
    "PLEX_MEDIA_CHANGED",
    "PLEX_SESSIONS_UNAVAILABLE",
]


class StructuredError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ClipCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_identity: str = Field(min_length=1)
    media_identity: str = Field(min_length=1)
    start_ms: int
    end_ms: int


class ClipCreateAccepted(BaseModel):
    accepted: bool
    code: str
    message: str
    job_id: str | None = None
    session_identity: str
    media_identity: str
    start_ms: int
    end_ms: int
    duration_ms: int
    validated_at: datetime


class ClipCreateValidationError(Exception):
    def __init__(
        self,
        code: StructuredErrorCode,
        message: str,
        *,
        status_code: int = 422,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error = StructuredError(code=code, message=message, retryable=retryable)


def validate_clip_create_request(
    request: ClipCreateRequest, snapshot: PlexSessionSnapshot
) -> ClipCreateAccepted:
    if request.start_ms < 0 or request.end_ms < 0:
        raise ClipCreateValidationError(
            "CLIP_RANGE_NEGATIVE",
            "Clip boundaries must be zero or later.",
        )
    if request.end_ms <= request.start_ms:
        raise ClipCreateValidationError(
            "CLIP_RANGE_ORDER",
            "End must be later than Start.",
        )

    if snapshot.status != "ok":
        raise ClipCreateValidationError(
            "PLEX_SESSIONS_UNAVAILABLE",
            snapshot.message,
            status_code=409,
            retryable=True,
        )

    session = _find_session(snapshot.sessions, request.session_identity)
    if session is None:
        raise ClipCreateValidationError(
            "PLEX_SESSION_NOT_FOUND",
            "The selected Plex session is no longer active.",
            status_code=409,
            retryable=True,
        )
    if session.media_identity != request.media_identity:
        raise ClipCreateValidationError(
            "PLEX_MEDIA_CHANGED",
            "The selected Plex player changed media. Capture the clip boundaries again.",
            status_code=409,
            retryable=False,
        )
    if session.duration_ms is not None and request.end_ms > session.duration_ms:
        raise ClipCreateValidationError(
            "CLIP_RANGE_DURATION_EXCEEDED",
            "End must be within the selected media duration.",
        )

    return ClipCreateAccepted(
        accepted=True,
        code="CLIP_REQUEST_VALIDATED",
        message=(
            "Clip request validated. Rendering will be queued once the durable job runner is "
            "available."
        ),
        session_identity=session.session_identity,
        media_identity=session.media_identity,
        start_ms=request.start_ms,
        end_ms=request.end_ms,
        duration_ms=request.end_ms - request.start_ms,
        validated_at=snapshot.sampled_at,
    )


def _find_session(
    sessions: list[PlexSession], session_identity: str
) -> PlexSession | None:
    return next(
        (
            session
            for session in sessions
            if session.session_identity == session_identity
        ),
        None,
    )
