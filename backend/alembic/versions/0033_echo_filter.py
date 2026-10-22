"""echo filter: hearsay state on memories and their independent origins (issue 137)

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-23 09:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("memory_item", sa.Column("belief_state", sa.String(length=10), server_default="fact", nullable=False))
    op.create_table(
        "memory_origin",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("memory_id", sa.UUID(), nullable=False),
        sa.Column("origin_root", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["memory_id"], ["memory_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("memory_id", "origin_root", name="uq_memory_origin_root"),
    )
    op.create_index(op.f("ix_memory_origin_agent_id"), "memory_origin", ["agent_id"], unique=False)
    op.create_index(op.f("ix_memory_origin_memory_id"), "memory_origin", ["memory_id"], unique=False)
    op.create_index(op.f("ix_memory_origin_tenant_id"), "memory_origin", ["tenant_id"], unique=False)

    from superaide.db.rls import enable

    enable(op.get_bind(), ("memory_origin",))


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), ("memory_origin",))
    op.drop_table("memory_origin")
    op.drop_column("memory_item", "belief_state")
