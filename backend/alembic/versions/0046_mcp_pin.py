"""mcp_pin: the hash of each MCP tool as the owner approved it (issue 116)

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-23 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from superaide.db.rls import disable, enable

revision: str = "0046"
down_revision: str | Sequence[str] | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_pin",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("skill_id", sa.String(length=120), nullable=False),
        sa.Column("server", sa.String(length=300), nullable=False),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "skill_id", "server", "tool", name="uq_mcp_pin"),
    )
    op.create_index(op.f("ix_mcp_pin_agent_id"), "mcp_pin", ["agent_id"])
    op.create_index(op.f("ix_mcp_pin_tenant_id"), "mcp_pin", ["tenant_id"])
    enable(op.get_bind(), ["mcp_pin"])


def downgrade() -> None:
    disable(op.get_bind(), ["mcp_pin"])
    op.drop_table("mcp_pin")
