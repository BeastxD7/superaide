"""action_log: hash-chained record of what the gateway did (issue 113)

The table is append-only at the database too: a trigger refuses UPDATE and DELETE.

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-23 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from superaide.db.rls import disable, enable

revision: str = "0044"
down_revision: str | Sequence[str] | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "action_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("effect", sa.String(length=20), nullable=False),
        sa.Column("args_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("approval_id", sa.UUID(), nullable=True),
        sa.Column("turn_id", sa.UUID(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "seq", name="uq_action_log_seq"),
    )
    op.create_index(op.f("ix_action_log_agent_id"), "action_log", ["agent_id"])
    op.create_index(op.f("ix_action_log_tenant_id"), "action_log", ["tenant_id"])
    op.execute(
        """
        CREATE FUNCTION action_log_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'action_log is append-only';
        END $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER action_log_no_change BEFORE UPDATE OR DELETE ON action_log "
        "FOR EACH ROW EXECUTE FUNCTION action_log_append_only()"
    )
    enable(op.get_bind(), ["action_log"])


def downgrade() -> None:
    disable(op.get_bind(), ["action_log"])
    op.execute("DROP TRIGGER action_log_no_change ON action_log")
    op.execute("DROP FUNCTION action_log_append_only()")
    op.drop_table("action_log")
