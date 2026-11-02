"""reception_message.deadline_at: keep the full moment of a deadline, not just its date (#490)

Revision ID: 0086
Revises: 0085
Create Date: 2026-11-02 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0086"
down_revision: str | Sequence[str] | None = "0085"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reception_message", sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "reception_message", sa.Column("deadline_parse", sa.String(length=10), server_default="", nullable=False)
    )
    op.alter_column("reception_message", "deadline_parse", server_default=None)


def downgrade() -> None:
    op.drop_column("reception_message", "deadline_parse")
    op.drop_column("reception_message", "deadline_at")
