"""reception: the owner's urgent-over-cap opt-in and when a message notified the owner (#477)

Revision ID: 0094
Revises: 0093
Create Date: 2026-11-19 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0094"
down_revision: str | Sequence[str] | None = "0093"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reception_settings", sa.Column("urgent_over_cap", sa.Boolean(), nullable=False, server_default="false")
    )
    op.add_column("reception_message", sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("reception_message", "notified_at")
    op.drop_column("reception_settings", "urgent_over_cap")
