"""memory_item live-notes index for the owner's notebook (ADR 0014)

Revision ID: 0080
Revises: 0079
Create Date: 2026-10-24 16:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0080"
down_revision: str | Sequence[str] | None = "0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_memory_item_live_created "
            "ON memory_item (agent_id, created_at DESC, id DESC) WHERE valid_to IS NULL AND conflict_status = 'none'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_memory_item_live_created")
