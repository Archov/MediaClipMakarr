"""Add persisted per-show/movie aspect-ratio overrides for crop detection.

Revision ID: 0011_aspect_ratio_overrides
Revises: 0010_trim_parent_link
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_aspect_ratio_overrides"
down_revision: str | None = "0010_trim_parent_link"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aspect_ratio_overrides",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("path_key", sa.Text(), nullable=True),
        sa.Column("plex_rating_key", sa.String(length=80), nullable=True),
        sa.Column("aspect_ratio", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_aspect_ratio_overrides_path_key", "aspect_ratio_overrides", ["path_key"]
    )
    op.create_index(
        "ix_aspect_ratio_overrides_plex_rating_key",
        "aspect_ratio_overrides",
        ["plex_rating_key"],
    )

    op.create_table(
        "aspect_ratio_override_external_ids",
        sa.Column("override_id", sa.String(length=80), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("override_id", "external_id"),
        sa.ForeignKeyConstraint(
            ["override_id"], ["aspect_ratio_overrides.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_aspect_ratio_override_external_ids_external_id",
        "aspect_ratio_override_external_ids",
        ["external_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_aspect_ratio_override_external_ids_external_id",
        table_name="aspect_ratio_override_external_ids",
    )
    op.drop_table("aspect_ratio_override_external_ids")
    op.drop_index(
        "ix_aspect_ratio_overrides_plex_rating_key", table_name="aspect_ratio_overrides"
    )
    op.drop_index("ix_aspect_ratio_overrides_path_key", table_name="aspect_ratio_overrides")
    op.drop_table("aspect_ratio_overrides")
