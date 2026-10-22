"""tool_trace: every tool call as hashes only (issue 110)

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-23 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from superaide.db.rls import disable, enable

revision: str = "0036"
down_revision: str | Sequence[str] | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_trace",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("turn_id", sa.UUID(), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("effect", sa.String(length=20), nullable=False),
        sa.Column("args_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("is_error", sa.Boolean(), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("skills", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("replay", sa.Boolean(), server_default="false", nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tool_trace_agent_id"), "tool_trace", ["agent_id"])
    op.create_index(op.f("ix_tool_trace_tenant_id"), "tool_trace", ["tenant_id"])
    op.create_index(op.f("ix_tool_trace_turn_id"), "tool_trace", ["turn_id"])
    enable(op.get_bind(), ["tool_trace"])


def downgrade() -> None:
    disable(op.get_bind(), ["tool_trace"])
    op.drop_table("tool_trace")
