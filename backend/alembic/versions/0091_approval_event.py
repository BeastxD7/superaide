"""approval_event: counts and timings of approval cards, no text or names (#407)

Revision ID: 0091
Revises: 0090
Create Date: 2026-11-04 22:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0091"
down_revision: str | Sequence[str] | None = "0090"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("approval_event",)
ONCE = sa.text("event IN ('shown', 'opened', 'decided')")


def upgrade() -> None:
    op.create_table(
        "approval_event",
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("event", sa.String(length=16), nullable=False),
        sa.Column("verdict", sa.String(length=8), nullable=True),
        sa.Column("via", sa.String(length=16), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("opened", sa.Boolean(), nullable=True),
        sa.Column("since_opened_ms", sa.Integer(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_approval_event_agent_created",
        "approval_event",
        ["agent_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index(op.f("ix_approval_event_agent_id"), "approval_event", ["agent_id"], unique=False)
    op.create_index(op.f("ix_approval_event_tenant_id"), "approval_event", ["tenant_id"], unique=False)
    op.create_index(
        "ux_approval_event_once", "approval_event", ["approval_id", "event"], unique=True, postgresql_where=ONCE
    )
    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
    op.drop_index("ux_approval_event_once", table_name="approval_event", postgresql_where=ONCE)
    op.drop_index(op.f("ix_approval_event_tenant_id"), table_name="approval_event")
    op.drop_index(op.f("ix_approval_event_agent_id"), table_name="approval_event")
    op.drop_index("ix_approval_event_agent_created", table_name="approval_event")
    op.drop_table("approval_event")
