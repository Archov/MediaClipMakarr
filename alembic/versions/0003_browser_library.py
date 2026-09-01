"""Add browser library metadata, thumbnails, and clip revision history.

Revision ID: 0003_browser_library
Revises: 0002_jobs_and_clips
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_browser_library"
down_revision: str | None = "0002_jobs_and_clips"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("clips") as batch:
        batch.add_column(sa.Column("custom_title", sa.Text(), nullable=True))
        batch.add_column(sa.Column("automatic_title", sa.Text(), nullable=True))
        batch.add_column(sa.Column("movie_title", sa.Text(), nullable=True))
        batch.add_column(sa.Column("movie_year", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("show_name", sa.Text(), nullable=True))
        batch.add_column(sa.Column("episode_title", sa.Text(), nullable=True))
        batch.add_column(sa.Column("season_number", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("episode_number", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("clip_number", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("plex_username", sa.Text(), nullable=True))
        batch.add_column(sa.Column("thumbnail_path", sa.Text(), nullable=True))
        batch.add_column(sa.Column("thumbnail_source_size", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("thumbnail_source_modified_ns", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("file_size_bytes", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("file_modified_ns", sa.Integer(), nullable=True))

    op.create_index("ix_clips_created_at", "clips", ["created_at"])
    op.execute("UPDATE clips SET automatic_title = title WHERE automatic_title IS NULL")
    op.create_index("ix_clips_library", "clips", ["library"])
    op.create_index("ix_clips_media_type", "clips", ["media_type"])
    op.create_index("ix_clips_title", "clips", ["title"])

    op.create_table(
        "clip_revisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("clip_id", sa.String(length=80), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["clip_id"], ["clips.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("clip_id", "revision", name="uq_clip_revisions_clip_revision"),
    )

    with op.batch_alter_table("pending_file_operations") as batch:
        batch.add_column(sa.Column("source_path", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("pending_file_operations") as batch:
        batch.drop_column("source_path")
    op.drop_table("clip_revisions")
    op.drop_index("ix_clips_title", table_name="clips")
    op.drop_index("ix_clips_media_type", table_name="clips")
    op.drop_index("ix_clips_library", table_name="clips")
    op.drop_index("ix_clips_created_at", table_name="clips")
    with op.batch_alter_table("clips") as batch:
        batch.drop_column("file_modified_ns")
        batch.drop_column("file_size_bytes")
        batch.drop_column("thumbnail_source_modified_ns")
        batch.drop_column("thumbnail_source_size")
        batch.drop_column("thumbnail_path")
        batch.drop_column("plex_username")
        batch.drop_column("clip_number")
        batch.drop_column("episode_number")
        batch.drop_column("season_number")
        batch.drop_column("episode_title")
        batch.drop_column("show_name")
        batch.drop_column("movie_year")
        batch.drop_column("movie_title")
        batch.drop_column("custom_title")
        batch.drop_column("automatic_title")
