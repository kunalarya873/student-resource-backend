"""0005_upload_progress_and_webhooks

Revision ID: 8c9d0e1f2a3b
Revises: 7b8c9d0e1f2a
Create Date: 2026-10-24 01:51:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8c9d0e1f2a3b"
down_revision: str | Sequence[str] | None = "7b8c9d0e1f2a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "upload_reservations",
        sa.Column("progress_percent", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "upload_reservations",
        sa.Column("resource_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_upload_reservations_resource_id",
        "upload_reservations",
        "resources",
        ["resource_id"],
        ["resource_id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "upload_reservations",
        sa.Column("error_message", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "upload_reservations",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("upload_reservations", "updated_at")
    op.drop_column("upload_reservations", "error_message")
    op.drop_constraint(
        "fk_upload_reservations_resource_id", "upload_reservations", type_="foreignkey"
    )
    op.drop_column("upload_reservations", "resource_id")
    op.drop_column("upload_reservations", "progress_percent")
