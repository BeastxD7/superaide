"""memory socket: one row per other assistant allowed to read Ava's memory read-only (#138)

A platform lookup table like ask_handle (read from a bearer token before an instance is known, so exempt from
Row-Level Security in db/rls.py EXEMPT). It holds a token hash, a name and a scope, and no memory. Nothing
reads or writes it unless MEMORY_SOCKET_ENABLED is on.

Revision ID: 0047
Revises: 0045
Create Date: 2026-10-23 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0047"
down_revision: str | Sequence[str] | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_socket_client",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("scope", sa.String(length=10), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_instance.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_memory_socket_client_agent_id"), "memory_socket_client", ["agent_id"], unique=False)
    op.create_index(op.f("ix_memory_socket_client_tenant_id"), "memory_socket_client", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_memory_socket_client_tenant_id"), table_name="memory_socket_client")
    op.drop_index(op.f("ix_memory_socket_client_agent_id"), table_name="memory_socket_client")
    op.drop_table("memory_socket_client")
