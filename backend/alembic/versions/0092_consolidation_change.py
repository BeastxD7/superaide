"""consolidation_change: an append-only change log of the nightly sleep job, with undo (#415)

Revision ID: 0092
Revises: 0091
Create Date: 2026-11-16 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0092"
down_revision: str | Sequence[str] | None = "0091"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("consolidation_change",)
UNDOES = sa.text("undoes_id IS NOT NULL")


def upgrade() -> None:
    op.create_table(
        "consolidation_change",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("owner_person_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("memory_id", sa.Uuid(), nullable=False),
        sa.Column("kept_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("undoes_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_person_id"], ["person.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["consolidation_run.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_consolidation_change_owner_created",
        "consolidation_change",
        ["agent_id", "owner_person_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index("ix_consolidation_change_run", "consolidation_change", ["run_id"], unique=False)
    op.create_index(
        "ux_consolidation_change_undoes", "consolidation_change", ["undoes_id"], unique=True, postgresql_where=UNDOES
    )
    op.create_index(op.f("ix_consolidation_change_agent_id"), "consolidation_change", ["agent_id"], unique=False)
    op.create_index(op.f("ix_consolidation_change_tenant_id"), "consolidation_change", ["tenant_id"], unique=False)
    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
    op.drop_index(op.f("ix_consolidation_change_tenant_id"), table_name="consolidation_change")
    op.drop_index(op.f("ix_consolidation_change_agent_id"), table_name="consolidation_change")
    op.drop_index("ux_consolidation_change_undoes", table_name="consolidation_change", postgresql_where=UNDOES)
    op.drop_index("ix_consolidation_change_run", table_name="consolidation_change")
    op.drop_index("ix_consolidation_change_owner_created", table_name="consolidation_change")
    op.drop_table("consolidation_change")
