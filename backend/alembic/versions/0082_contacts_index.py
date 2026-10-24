"""contact (owner_person_id, vip desc) for the kept contacts: the owner's people list (ADR 0014)

Revision ID: 0082
Revises: 0081
Create Date: 2026-10-24 18:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0082"
down_revision: str | Sequence[str] | None = "0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_contact_owner_active "
            "ON contact (owner_person_id, vip DESC) WHERE removed_at IS NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_contact_owner_active")
