"""approvals carry the hash of what the owner was shown, and an optional expiry (issue 111)

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-23 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0035"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("approval", sa.Column("bound_hash", sa.String(length=64), nullable=True))
    op.add_column("approval", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("approval", "expires_at")
    op.drop_column("approval", "bound_hash")
