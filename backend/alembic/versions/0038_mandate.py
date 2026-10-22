"""mandate: signed narrowing tokens the owner issued (issue 112)

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-23 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from superaide.db.rls import disable, enable

revision: str = "0038"
down_revision: str | Sequence[str] | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mandate",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("card", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("uses", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_mandate_agent_id"), "mandate", ["agent_id"])
    op.create_index(op.f("ix_mandate_tenant_id"), "mandate", ["tenant_id"])
    enable(op.get_bind(), ["mandate"])


def downgrade() -> None:
    disable(op.get_bind(), ["mandate"])
    op.drop_table("mandate")
