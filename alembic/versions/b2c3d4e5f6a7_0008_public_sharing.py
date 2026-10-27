"""0008_public_sharing

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-28 00:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Update folders table
    op.add_column(
        "folders",
        sa.Column("is_public", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "folders",
        sa.Column("share_token", sa.String(length=64), nullable=True),
    )
    op.create_index("idx_folders_is_public", "folders", ["is_public"])
    op.create_index("idx_folders_share_token", "folders", ["share_token"], unique=True)

    # 2. Update resources table
    op.add_column(
        "resources",
        sa.Column("is_public", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "resources",
        sa.Column("share_token", sa.String(length=64), nullable=True),
    )
    op.create_index("idx_resources_is_public", "resources", ["is_public"])
    op.create_index("idx_resources_share_token", "resources", ["share_token"], unique=True)


def downgrade() -> None:
    op.drop_index("idx_resources_share_token", table_name="resources")
    op.drop_index("idx_resources_is_public", table_name="resources")
    op.drop_column("resources", "share_token")
    op.drop_column("resources", "is_public")

    op.drop_index("idx_folders_share_token", table_name="folders")
    op.drop_index("idx_folders_is_public", table_name="folders")
    op.drop_column("folders", "share_token")
    op.drop_column("folders", "is_public")
