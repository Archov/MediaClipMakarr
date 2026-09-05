"""Record the direct parent of clips created by an edit.

Revision ID: 0010_trim_parent_link
Revises: 0009_bulk_immich_upload_active_unique
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_trim_parent_link"
down_revision: str | None = "0009_bulk_immich_upload_active_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("clips") as batch:
        batch.add_column(sa.Column("parent_clip_id", sa.String(length=80), nullable=True))
        batch.create_foreign_key(
            "fk_clips_parent_clip_id",
            "clips",
            ["parent_clip_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_clips_parent_clip_id", "clips", ["parent_clip_id"])


def downgrade() -> None:
    op.drop_index("ix_clips_parent_clip_id", table_name="clips")
    with op.batch_alter_table("clips") as batch:
        batch.drop_constraint("fk_clips_parent_clip_id", type_="foreignkey")
        batch.drop_column("parent_clip_id")
