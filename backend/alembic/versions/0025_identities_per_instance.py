"""identities and conversations per instance (ADR 0009 T7)

The same Telegram or Slack account talking to two owners' Avas is a separate identity,
and a group both bots sit in a separate conversation, in each instance. Today every
instance has its own tenant, so no existing rows collide.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-22 08:30:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_identity_key", "channel_identity", type_="unique")
    op.create_unique_constraint(
        "uq_identity_key", "channel_identity", ["agent_id", "provider", "tenant_scope", "stable_key"]
    )
    op.drop_constraint("uq_context_key", "context", type_="unique")
    op.create_unique_constraint("uq_context_key", "context", ["agent_id", "provider", "tenant_scope", "external_id"])


def downgrade() -> None:
    op.drop_constraint("uq_context_key", "context", type_="unique")
    op.create_unique_constraint("uq_context_key", "context", ["tenant_id", "provider", "tenant_scope", "external_id"])
    op.drop_constraint("uq_identity_key", "channel_identity", type_="unique")
    op.create_unique_constraint(
        "uq_identity_key", "channel_identity", ["tenant_id", "provider", "tenant_scope", "stable_key"]
    )
