"""emotional use: a counter of heavy conversations, kind only (issue 124)

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-23 14:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0037"
down_revision: str | Sequence[str] | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "emotional_use",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["person.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for col in ("agent_id", "tenant_id", "person_id"):
        op.create_index(op.f(f"ix_emotional_use_{col}"), "emotional_use", [col], unique=False)

    from superaide.db.rls import enable

    enable(op.get_bind(), ("emotional_use",))


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), ("emotional_use",))
    op.drop_table("emotional_use")
