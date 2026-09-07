"""Record the letterbox/pillarbox crop (if any) a clip was rendered with.

Revision ID: 0012_clip_crop
Revises: 0011_aspect_ratio_overrides
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_clip_crop"
down_revision: str | None = "0011_aspect_ratio_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("clips") as batch:
        batch.add_column(sa.Column("crop_width", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("crop_height", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("crop_x", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("crop_y", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("clips") as batch:
        batch.drop_column("crop_y")
        batch.drop_column("crop_x")
        batch.drop_column("crop_height")
        batch.drop_column("crop_width")
