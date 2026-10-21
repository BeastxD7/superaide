"""recall search: pg_trgm for typos, full-text indexes on notes and messages

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-22 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    # Optional: recall checks for it and works without it (no typo matching).
    if conn.scalar(sa.text("SELECT count(*) FROM pg_available_extensions WHERE name = 'pg_trgm'")):
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_item_fts ON memory_item USING gin (to_tsvector('english', content))"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_event_fts ON event USING gin (to_tsvector('english', content))")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_event_fts")
    op.execute("DROP INDEX IF EXISTS ix_memory_item_fts")
