"""event (context_id, created_at desc, id desc): keyset paging of a conversation (ADR 0014)

Revision ID: 0077
Revises: 0076
Create Date: 2026-10-24 13:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0077"
down_revision: str | Sequence[str] | None = "0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CONCURRENTLY: the event table is the largest one, and a plain CREATE INDEX blocks writes while it builds.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_event_context_created "
            "ON event (context_id, created_at DESC, id DESC)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_event_context_created")
