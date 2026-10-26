"""0007_folder_sharing

Revision ID: a1b2c3d4e5f6
Revises: 9d0e1f2a3b4c
Create Date: 2026-10-26 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "9d0e1f2a3b4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "folder_access",
        sa.Column("access_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "folder_id",
            UUID(as_uuid=True),
            sa.ForeignKey("folders.folder_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("permission", sa.String(length=30), server_default="VIEW", nullable=False),
        sa.Column(
            "granted_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("folder_id", "user_id", name="uq_folder_user_access"),
    )
    op.create_index("idx_folder_access_user_id", "folder_access", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_folder_access_user_id", table_name="folder_access")
    op.drop_table("folder_access")
