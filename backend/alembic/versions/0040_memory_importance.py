"""memory_item: importance, time_bound and spaced re-verification (#49)

Additive columns with defaults, so existing rows keep working: importance 0.5, not time bound,
never verified. No new table; the columns ride on memory_item's existing Row-Level Security.

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-23 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0040"
down_revision: str | Sequence[str] | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("memory_item", sa.Column("importance", sa.Float(), server_default="0.5", nullable=False))
    op.add_column("memory_item", sa.Column("time_bound", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("memory_item", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("memory_item", sa.Column("verify_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("memory_item", sa.Column("recheck_asked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for col in ("recheck_asked_at", "verify_count", "last_verified_at", "time_bound", "importance"):
        op.drop_column("memory_item", col)
