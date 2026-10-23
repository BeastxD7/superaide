"""lock a fact: the owner can pin a note so nothing automatic merges, retires or fades it (#55)

Revision ID: 0071
Revises: 0070
Create Date: 2026-10-24 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0071"
down_revision: str | Sequence[str] | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("memory_item", sa.Column("locked", sa.Boolean(), server_default="false", nullable=False))


def downgrade() -> None:
    op.drop_column("memory_item", "locked")
