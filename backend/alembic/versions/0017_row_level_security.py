"""row-level security: each agent instance sees only its own rows (ADR 0009 T1)

Turns on one policy per table with agent_id: rows are visible and writable only when
agent_id equals the transaction's app.agent_id (set by the app at the start of every
transaction). It binds only roles that are neither superuser nor BYPASSRLS, so it
changes nothing until the app connects as its own role (`superaide db-app-role`).

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-22 04:00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen here: later tables are enabled by their own migrations.
TABLES = (
    "agent_state",
    "approval",
    "audit_log",
    "channel_identity",
    "channel_invite",
    "contact",
    "context",
    "document",
    "event",
    "identity_link_event",
    "installed_skill",
    "intention",
    "job",
    "link_challenge",
    "llm_provider",
    "memory_embedding",
    "memory_item",
    "model_role",
    "onboarding",
    "owner_model_price",
    "owner_preference",
    "person",
    "spend_ledger",
)


def upgrade() -> None:
    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
