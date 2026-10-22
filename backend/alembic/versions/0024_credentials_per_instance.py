"""credentials per instance: each hired Ava's own connections (ADR 0009 T3)

Existing credentials belong to the instance of their tenant (the server's first one).
They stay readable: they were sealed without the instance in the encryption's associated
data ("legacy"), and the app reseals each with its instance the next time it reads it, so
this migration never needs the vault's master key. Row-Level Security covers the table.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-22 07:30:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_AGENT_ID = "00000000-0000-4000-8000-000000000002"


def upgrade() -> None:
    from superaide.db.rls import enable

    op.add_column("integration_secret", sa.Column("agent_id", sa.UUID(), nullable=True))
    op.add_column(
        "integration_secret", sa.Column("aad", sa.String(length=10), server_default="instance", nullable=False)
    )
    op.execute(
        "UPDATE integration_secret s SET aad = 'legacy', agent_id = COALESCE("
        "(SELECT i.id FROM agent_instance i WHERE i.tenant_id = s.tenant_id ORDER BY i.created_at LIMIT 1), "
        f"'{DEFAULT_AGENT_ID}'::uuid)"
    )
    op.alter_column("integration_secret", "agent_id", nullable=False)
    op.create_index(op.f("ix_integration_secret_agent_id"), "integration_secret", ["agent_id"], unique=False)
    op.drop_constraint("uq_integration_secret_slot", "integration_secret", type_="unique")
    op.create_unique_constraint("uq_integration_secret_slot", "integration_secret", ["agent_id", "provider", "name"])
    enable(op.get_bind(), ["integration_secret"])


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), ["integration_secret"])
    op.drop_constraint("uq_integration_secret_slot", "integration_secret", type_="unique")
    op.create_unique_constraint("uq_integration_secret_slot", "integration_secret", ["tenant_id", "provider", "name"])
    op.drop_index(op.f("ix_integration_secret_agent_id"), table_name="integration_secret")
    op.drop_column("integration_secret", "aad")
    op.drop_column("integration_secret", "agent_id")
