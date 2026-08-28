"""Add durable jobs and clips.

Revision ID: 0002_jobs_and_clips
Revises: 0001_bootstrap
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_jobs_and_clips"
down_revision: str | None = "0001_bootstrap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False, index=True),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("current_stage_progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("run_token", sa.String(length=80), nullable=True),
        sa.Column("render_plan_json", sa.Text(), nullable=False),
        sa.Column("render_plan_hash", sa.String(length=64), nullable=False),
        sa.Column("finalizing_clip_id", sa.String(length=80), nullable=True),
        sa.Column("finalizing_revision", sa.Integer(), nullable=True),
        sa.Column("finalizing_destination", sa.Text(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_jobs_state_created_at", "jobs", ["state", "created_at"])

    op.create_table(
        "clips",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("library", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(length=40), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False, unique=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source_start_ms", sa.Integer(), nullable=False),
        sa.Column("source_end_ms", sa.Integer(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_size_bytes", sa.Integer(), nullable=False),
        sa.Column("source_modified_at", sa.DateTime(), nullable=False),
        sa.Column("selected_audio_stream_index", sa.Integer(), nullable=False),
        sa.Column("render_plan_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "pending_file_operations",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("job_id", sa.String(length=80), nullable=False),
        sa.Column("clip_id", sa.String(length=80), nullable=False),
        sa.Column("operation_type", sa.String(length=40), nullable=False),
        sa.Column("temp_path", sa.Text(), nullable=False),
        sa.Column("target_path", sa.Text(), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("render_plan_hash", sa.String(length=64), nullable=False),
        sa.Column("clip_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("pending_file_operations")
    op.drop_table("clips")
    op.drop_index("ix_jobs_state_created_at", table_name="jobs")
    op.drop_table("jobs")
