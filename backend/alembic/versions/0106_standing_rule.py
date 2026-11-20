"""standing_rule: owner-declared statements Ava may say and topics she must refuse (#486)

Revision ID: 0106
Revises: 0105
Create Date: 2026-11-20 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

TABLES = ("standing_rule",)

# revision identifiers, used by Alembic.
revision: str = "0106"
down_revision: str | Sequence[str] | None = "0105"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "standing_rule",
        sa.Column("owner_person_id", sa.UUID(), nullable=False),
        sa.Column("scope", sa.String(length=10), nullable=False),
        sa.Column("contact_person_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("text", sa.String(length=160), nullable=False),
        sa.Column("topic", sa.String(length=24), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["contact_person_id"], ["person.id"]),
        sa.ForeignKeyConstraint(["owner_person_id"], ["person.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_standing_rule_agent_id"), "standing_rule", ["agent_id"], unique=False)
    op.create_index(
        "ix_standing_rule_agent_live", "standing_rule", ["agent_id", "revoked_at", "expires_at"], unique=False
    )
    op.create_index(op.f("ix_standing_rule_owner_person_id"), "standing_rule", ["owner_person_id"], unique=False)
    op.create_index(op.f("ix_standing_rule_tenant_id"), "standing_rule", ["tenant_id"], unique=False)
    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    """Downgrade schema."""
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
    op.drop_index(op.f("ix_standing_rule_tenant_id"), table_name="standing_rule")
    op.drop_index(op.f("ix_standing_rule_owner_person_id"), table_name="standing_rule")
    op.drop_index("ix_standing_rule_agent_live", table_name="standing_rule")
    op.drop_index(op.f("ix_standing_rule_agent_id"), table_name="standing_rule")
    op.drop_table("standing_rule")
