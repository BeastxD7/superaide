"""audit_log paging indexes: the safety log and the owner's access log (ADR 0014)

Revision ID: 0081
Revises: 0080
Create Date: 2026-10-24 17:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0081"
down_revision: str | Sequence[str] | None = "0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_audit_log_agent_created "
            "ON audit_log (agent_id, created_at DESC, id DESC)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_audit_log_operator_access "
            "ON audit_log (agent_id, created_at DESC, id DESC) WHERE actor = 'operator' AND action = 'operator_access'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_audit_log_operator_access")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_audit_log_agent_created")
