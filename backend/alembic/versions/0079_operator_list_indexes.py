"""operator list paging indexes: memory_item, approval, person, unlinked channel_identity (ADR 0014)

Revision ID: 0079
Revises: 0078
Create Date: 2026-10-24 15:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0079"
down_revision: str | Sequence[str] | None = "0078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = {
    "ix_memory_item_agent_created": "memory_item (agent_id, created_at DESC, id DESC)",
    "ix_approval_agent_created": "approval (agent_id, created_at DESC, id DESC)",
    "ix_person_agent_created": "person (agent_id, created_at, id)",
    "ix_identity_unlinked": "channel_identity (agent_id, created_at, id) WHERE person_id IS NULL",
}


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, spec in INDEXES.items():
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {spec}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
