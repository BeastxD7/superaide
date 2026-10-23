"""context.last_event_at and the operator paging indexes (ADR 0014)

event (agent_id, created_at desc, id desc) serves /api/events without a conversation; context.last_event_at keeps
"when did this conversation last move" so the console lists conversations by activity without a GROUP BY over every
event, and ix_context_activity (agent_id, last_event_at desc, id desc) is the index that listing pages by.

Revision ID: 0078
Revises: 0077
Create Date: 2026-10-24 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0078"
down_revision: str | Sequence[str] | None = "0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Adding a column with a constant-expression default is metadata-only in Postgres 11+: no table rewrite.
    op.add_column(
        "context",
        sa.Column("last_event_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    # Backfill in one statement: the newest event of each conversation (an index-only max per context through
    # ix_event_context_created, 0077), else the day the conversation was made. `context` has one row per
    # conversation, not per message, so this is thousands of rows for an owner and a few million for a whole
    # platform: one pass. If a hosted platform ever has tens of millions, run it in id ranges instead.
    op.execute(
        "UPDATE context SET last_event_at = COALESCE("
        "(SELECT max(e.created_at) FROM event e WHERE e.context_id = context.id), context.created_at)"
    )
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_context_activity "
            "ON context (agent_id, last_event_at DESC, id DESC)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_event_agent_created "
            "ON event (agent_id, created_at DESC, id DESC)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_event_agent_created")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_context_activity")
    op.drop_column("context", "last_event_at")
