"""0003_full_text_search

Revision ID: de614464e9ad
Revises: 579475e8e451
Create Date: 2026-10-23 23:00:11.888141

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "de614464e9ad"
down_revision: str | Sequence[str] | None = "579475e8e451"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: Create PostgreSQL GIN index for full-text search on resources."""
    op.execute("""
        CREATE INDEX idx_resources_fts ON resources 
        USING gin(to_tsvector('english', title || ' ' || coalesce(description, '')));
    """)


def downgrade() -> None:
    """Downgrade schema: Drop GIN index on resources."""
    op.execute("DROP INDEX IF EXISTS idx_resources_fts;")
