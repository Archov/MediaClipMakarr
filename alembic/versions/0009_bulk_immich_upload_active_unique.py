"""Enforce at most one active bulk_immich_upload job at the database layer.

Bulk uploads now run concurrently with the rest of the job queue (see
JobRunner._run's background-task dispatch for bulk_immich_upload), so two
bulk jobs enqueued in the same narrow race window can now genuinely execute
at the same time — not just redundantly re-validate after one another, as
under the previous fully-sequential runner. The application-level "check
then insert" dedup in enqueue_bulk_immich_upload_job is not atomic across
two separate transactions; this partial unique index closes that race so a
concurrent duplicate insert fails instead of silently succeeding.

Revision ID: 0009_bulk_immich_upload_active_unique
Revises: 0008_immich_tag_ids
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_bulk_immich_upload_active_unique"
down_revision: str | None = "0008_immich_tag_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_jobs_active_bulk_immich_upload",
        "jobs",
        ["render_plan_hash"],
        unique=True,
        sqlite_where=sa.text(
            "type = 'bulk_immich_upload' AND state IN ('QUEUED', 'RUNNING', 'FINALIZING')"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_active_bulk_immich_upload", table_name="jobs")
