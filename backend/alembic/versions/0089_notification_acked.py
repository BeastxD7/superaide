"""notification_delivery.acked_at: the owner saw it (#491)

Revision ID: 0089
Revises: 0088
Create Date: 2026-11-04 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0089"
down_revision: str | Sequence[str] | None = "0088"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("notification_delivery", sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("notification_delivery", "acked_at")
