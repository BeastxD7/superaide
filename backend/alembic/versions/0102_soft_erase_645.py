"""soft_erase: erase hides and keeps; retired marks on documents, notes, messages (#645)

Revision ID: 0102
Revises: 0101
Create Date: 2026-11-20 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0102"
down_revision: str | Sequence[str] | None = "0101"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("document", "memory_item", "event")


def upgrade() -> None:
    """Upgrade schema. Nullable columns on tables that already carry tenant_id/agent_id and RLS: policies unchanged."""
    for table in TABLES:
        op.add_column(table, sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("erased_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("contact", sa.Column("notes_hidden", sa.Text(), server_default="", nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("contact", "notes_hidden")
    for table in reversed(TABLES):
        op.drop_column(table, "erased_by")
        op.drop_column(table, "erased_at")
