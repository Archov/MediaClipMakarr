"""Public job-system interfaces."""

from mediaclipmakarr.clip_library import BulkImmichUploadJobPlan

from .events import JobEventBroker, job_sse_payload
from .models import ClaimedJob, JobError, JobSnapshot, JobUpdateConflict
from .recovery import fail_abandoned_jobs, recover_finalizing_jobs
from .repository import (
    claim_next_job,
    create_pending_operation,
    enqueue_bulk_immich_upload_job,
    enqueue_clip_create_job,
    enqueue_immich_upload_job,
    enqueue_metadata_edit_job,
    enqueue_thumbnail_job,
    fail_job,
    finish_job_success,
    finish_job_success_without_token,
    finish_running_job_partial,
    get_job_snapshot,
    get_latest_jobs_for_operations,
    record_immich_upload_result,
    transition_to_finalizing,
    update_running_job,
)
from .runner import ImmichJobSettings, JobRunner

__all__ = [
    "BulkImmichUploadJobPlan",
    "ClaimedJob",
    "ImmichJobSettings",
    "JobError",
    "JobEventBroker",
    "JobRunner",
    "JobSnapshot",
    "JobUpdateConflict",
    "claim_next_job",
    "create_pending_operation",
    "enqueue_bulk_immich_upload_job",
    "enqueue_clip_create_job",
    "enqueue_immich_upload_job",
    "enqueue_metadata_edit_job",
    "enqueue_thumbnail_job",
    "fail_abandoned_jobs",
    "fail_job",
    "finish_job_success",
    "finish_job_success_without_token",
    "finish_running_job_partial",
    "get_job_snapshot",
    "get_latest_jobs_for_operations",
    "job_sse_payload",
    "record_immich_upload_result",
    "recover_finalizing_jobs",
    "transition_to_finalizing",
    "update_running_job",
]

