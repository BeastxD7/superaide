"""glass brain share link: an unlisted, expiring, revocable link to the counts-only picture (#55)

A platform lookup table like memory_socket_client (read from a token before an instance is known, so exempt
from Row-Level Security in db/rls.py EXEMPT). It holds a token hash, an expiry of at most 7 days (a check
enforces it) and a revoked time, and no memory. Nothing reads or writes it unless BRAIN_SHARE_ENABLED is on.

Revision ID: 0072
Revises: 0071
Create Date: 2026-10-24 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0072"
down_revision: str | Sequence[str] | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brain_share",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("owner_person_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("views", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '7 days'", name="ck_brain_share_max_7_days"
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_instance.id"]),
        sa.ForeignKeyConstraint(["owner_person_id"], ["person.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_brain_share_agent_id"), "brain_share", ["agent_id"], unique=False)
    op.create_index(op.f("ix_brain_share_tenant_id"), "brain_share", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_brain_share_tenant_id"), table_name="brain_share")
    op.drop_index(op.f("ix_brain_share_agent_id"), table_name="brain_share")
    op.drop_table("brain_share")
