"""turn_timing: per-stage milliseconds of every message turn, numbers only (#412)

Revision ID: 0089
Revises: 0088
Create Date: 2026-11-04 21:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0089"
down_revision: str | Sequence[str] | None = "0088"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("turn_timing",)
STAGES = ("verify", "identity", "gate", "retrieval", "airlock", "model", "policy", "reply", "send")


def upgrade() -> None:
    op.create_table(
        "turn_timing",
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("total_ms", sa.Integer(), nullable=False),
        *(sa.Column(f"{name}_ms", sa.Integer(), nullable=True) for name in STAGES),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("first_token_ms", sa.Integer(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_turn_timing_agent_created",
        "turn_timing",
        ["agent_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index(op.f("ix_turn_timing_agent_id"), "turn_timing", ["agent_id"], unique=False)
    op.create_index(op.f("ix_turn_timing_tenant_id"), "turn_timing", ["tenant_id"], unique=False)
    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
    op.drop_index(op.f("ix_turn_timing_tenant_id"), table_name="turn_timing")
    op.drop_index(op.f("ix_turn_timing_agent_id"), table_name="turn_timing")
    op.drop_index("ix_turn_timing_agent_created", table_name="turn_timing")
    op.drop_table("turn_timing")
