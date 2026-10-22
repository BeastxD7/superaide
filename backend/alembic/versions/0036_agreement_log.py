"""agreement log: replies that judged the owner's own claim, for the measured agreement rate (issue 123)

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-23 13:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0036"
down_revision: str | Sequence[str] | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agreement_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("validated", sa.Boolean(), nullable=False),
        sa.Column("second_stance", sa.String(length=10), nullable=False),
        sa.Column("flagged", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["person.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for col in ("agent_id", "tenant_id", "person_id"):
        op.create_index(op.f(f"ix_agreement_log_{col}"), "agreement_log", [col], unique=False)

    from superaide.db.rls import enable

    enable(op.get_bind(), ("agreement_log",))


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), ("agreement_log",))
    op.drop_table("agreement_log")
