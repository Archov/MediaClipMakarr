"""Enforce at most one active immich_upload job per clip at the database layer.

The application-level "check then insert" dedup in enqueue_immich_upload_job
is not atomic across two separate transactions; this partial unique index
closes that race so a concurrent duplicate insert fails instead of silently
succeeding.

Revision ID: 0007_immich_upload_active_unique
Revises: 0006_immich_asset_id
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_immich_upload_active_unique"
down_revision: str | None = "0006_immich_asset_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_jobs_active_immich_upload",
        "jobs",
        ["render_plan_hash"],
        unique=True,
        sqlite_where=sa.text(
            "type = 'immich_upload' AND state IN ('QUEUED', 'RUNNING', 'FINALIZING')"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_active_immich_upload", table_name="jobs")
