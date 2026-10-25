"""0004_db_performance_indexes

Revision ID: 7b8c9d0e1f2a
Revises: de614464e9ad
Create Date: 2026-10-24 00:03:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7b8c9d0e1f2a"
down_revision: str | Sequence[str] | None = "de614464e9ad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply performance indexes across folders, resources, sharing, sweeper, and audit logs."""
    # 1. Folder navigation & child resource lookup optimization
    op.create_index("idx_resources_folder_id", "resources", ["folder_id"], unique=False)
    op.create_index("idx_folders_parent_id", "folders", ["parent_folder_id"], unique=False)

    # 2. Peer sharing reverse lookup for "Shared with me" queries
    op.create_index("idx_resource_access_user_id", "resource_access", ["user_id"], unique=False)

    # 3. High-efficiency partial index for expired upload reservation sweeper
    op.create_index(
        "idx_upload_res_pending_exp",
        "upload_reservations",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    # 4. Audit trail reverse-chronological sorting
    op.execute("CREATE INDEX idx_audit_events_created_at_desc ON audit_events (created_at DESC);")


def downgrade() -> None:
    """Drop performance indexes."""
    op.execute("DROP INDEX IF EXISTS idx_audit_events_created_at_desc;")
    op.drop_index("idx_upload_res_pending_exp", table_name="upload_reservations")
    op.drop_index("idx_resource_access_user_id", table_name="resource_access")
    op.drop_index("idx_folders_parent_id", table_name="folders")
    op.drop_index("idx_resources_folder_id", table_name="resources")
