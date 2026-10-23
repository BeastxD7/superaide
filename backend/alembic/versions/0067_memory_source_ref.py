"""memory_item.source_ref: the calendar event or email a short fact was read from

Revision ID: 0067
Revises: 0066
Create Date: 2026-10-24 10:00:00

Only the item's id ("calendar:<id>", "gmail:<id>"), never its text. Disconnecting the source deletes by it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0067"
down_revision: str | Sequence[str] | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("memory_item", sa.Column("source_ref", sa.String(length=200), nullable=True))
    op.create_index(op.f("ix_memory_item_source_ref"), "memory_item", ["source_ref"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_memory_item_source_ref"), table_name="memory_item")
    op.drop_column("memory_item", "source_ref")
