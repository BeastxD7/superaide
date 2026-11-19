"""friends: accounts listed for a contact, unverified-friend candidates, invite purpose (#479, #504)

Revision ID: 0100
Revises: 0099
Create Date: 2026-11-20 03:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

TABLES = ("contact_handle", "friend_candidate")

# revision identifiers, used by Alembic.
revision: str = "0100"
down_revision: str | Sequence[str] | None = "0099"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "contact_handle",
        sa.Column("contact_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("value", sa.String(length=200), nullable=False),
        *_base(),
        sa.ForeignKeyConstraint(["contact_id"], ["contact.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "provider", "kind", "value", name="uq_contact_handle_value"),
    )
    op.create_table(
        "friend_candidate",
        sa.Column("identity_id", sa.UUID(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=False),
        sa.Column("via", sa.String(length=10), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        *_base(),
        sa.ForeignKeyConstraint(["identity_id"], ["channel_identity.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["contact.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identity_id", name="uq_friend_candidate_identity"),
    )
    for table in TABLES:
        op.create_index(op.f(f"ix_{table}_agent_id"), table, ["agent_id"], unique=False)
        op.create_index(op.f(f"ix_{table}_tenant_id"), table, ["tenant_id"], unique=False)
    op.create_index(op.f("ix_contact_handle_contact_id"), "contact_handle", ["contact_id"], unique=False)
    op.create_index(op.f("ix_friend_candidate_identity_id"), "friend_candidate", ["identity_id"], unique=False)
    op.create_index(op.f("ix_friend_candidate_contact_id"), "friend_candidate", ["contact_id"], unique=False)
    op.add_column("channel_invite", sa.Column("purpose", sa.String(length=10), server_default="link", nullable=False))
    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    """Downgrade schema."""
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
    op.drop_column("channel_invite", "purpose")
    op.drop_table("friend_candidate")
    op.drop_table("contact_handle")
