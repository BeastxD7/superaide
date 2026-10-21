"""where each reminder is delivered besides the app

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-22 18:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing reminders keep following the owner's interruption choice.
    op.add_column("intention", sa.Column("channel", sa.String(length=12), server_default="default", nullable=False))


def downgrade() -> None:
    op.drop_column("intention", "channel")
