"""memory influence: which notes changed a reply, from ablation replay (issue 122)

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-23 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0035"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_influence",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("memory_id", sa.UUID(), nullable=False),
        sa.Column("influence", sa.Float(), nullable=False),
        sa.Column("changed", sa.Boolean(), nullable=False),
        sa.Column("ablations", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["event.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["memory_id"], ["memory_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "memory_id", name="uq_memory_influence"),
    )
    for col in ("agent_id", "tenant_id", "event_id", "memory_id"):
        op.create_index(op.f(f"ix_memory_influence_{col}"), "memory_influence", [col], unique=False)

    from superaide.db.rls import enable

    enable(op.get_bind(), ("memory_influence",))


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), ("memory_influence",))
    op.drop_table("memory_influence")
