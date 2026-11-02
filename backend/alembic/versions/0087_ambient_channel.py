"""ambient_channel: channels the owner let Ava read in full, off by default (#509)

Revision ID: 0087
Revises: 0086
Create Date: 2026-11-02 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0087"
down_revision: str | Sequence[str] | None = "0086"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("ambient_channel",)


def upgrade() -> None:
    op.create_table(
        "ambient_channel",
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("tenant_scope", sa.String(length=100), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled_by_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_heard_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "provider", "tenant_scope", "external_id", name="uq_ambient_channel_key"),
    )
    op.create_index(op.f("ix_ambient_channel_agent_id"), "ambient_channel", ["agent_id"], unique=False)
    op.create_index(op.f("ix_ambient_channel_tenant_id"), "ambient_channel", ["tenant_id"], unique=False)
    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
    op.drop_index(op.f("ix_ambient_channel_tenant_id"), table_name="ambient_channel")
    op.drop_index(op.f("ix_ambient_channel_agent_id"), table_name="ambient_channel")
    op.drop_table("ambient_channel")
