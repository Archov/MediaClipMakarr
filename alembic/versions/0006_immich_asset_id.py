"""Add Immich asset association columns to clips.

Revision ID: 0006_immich_asset_id
Revises: 0005_movie_directory_backfill
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_immich_asset_id"
down_revision: str | None = "0005_movie_directory_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("clips") as batch:
        batch.add_column(sa.Column("immich_asset_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("immich_server_url", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("clips") as batch:
        batch.drop_column("immich_server_url")
        batch.drop_column("immich_asset_id")
