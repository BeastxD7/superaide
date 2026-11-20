"""draft_tone: owner tone setting for drafted replies (#683)

Revision ID: 0106
Revises: 0105
Create Date: 2026-11-20 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0106"
down_revision: str | Sequence[str] | None = "0105"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Two columns on owner_preference, which already carries tenant_id/agent_id and RLS: policies unchanged."""
    op.add_column(
        "owner_preference", sa.Column("draft_tone", sa.String(length=12), server_default="neutral", nullable=False)
    )
    op.add_column(
        "owner_preference",
        sa.Column("draft_tone_source", sa.String(length=10), server_default="default", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("owner_preference", "draft_tone_source")
    op.drop_column("owner_preference", "draft_tone")
