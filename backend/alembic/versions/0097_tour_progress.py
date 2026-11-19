"""tour_progress: which guided tours an owner finished or skipped (#626)

Revision ID: 0097
Revises: 0096
Create Date: 2026-11-19 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

TABLES = ("tour_progress",)

# revision identifiers, used by Alembic.
revision: str = "0097"
down_revision: str | Sequence[str] | None = "0096"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "tour_progress",
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("tour", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["person.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("person_id", "tour", name="uq_tour_progress_person_tour"),
    )
    op.create_index(op.f("ix_tour_progress_agent_id"), "tour_progress", ["agent_id"], unique=False)
    op.create_index(op.f("ix_tour_progress_person_id"), "tour_progress", ["person_id"], unique=False)
    op.create_index(op.f("ix_tour_progress_tenant_id"), "tour_progress", ["tenant_id"], unique=False)
    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    """Downgrade schema."""
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
    op.drop_index(op.f("ix_tour_progress_tenant_id"), table_name="tour_progress")
    op.drop_index(op.f("ix_tour_progress_person_id"), table_name="tour_progress")
    op.drop_index(op.f("ix_tour_progress_agent_id"), table_name="tour_progress")
    op.drop_table("tour_progress")
