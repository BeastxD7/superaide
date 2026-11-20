"""booking_request: a visitor's request for time with the owner (#496)

Revision ID: 0103
Revises: 0102
Create Date: 2026-11-20 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

TABLES = ("booking_request",)

# revision identifiers, used by Alembic.
revision: str = "0103"
down_revision: str | Sequence[str] | None = "0102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "booking_request",
        sa.Column("identity_id", sa.UUID(), nullable=False),
        sa.Column("context_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("tz_name", sa.String(length=64), nullable=False),
        sa.Column("visitor_name", sa.String(length=40), nullable=False),
        sa.Column("visitor_email", sa.String(length=254), nullable=False),
        sa.Column("slots", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("chosen", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("approval_id", sa.UUID(), nullable=True),
        sa.Column("event_id", sa.String(length=1024), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["context_id"], ["context.id"]),
        sa.ForeignKeyConstraint(["identity_id"], ["channel_identity.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_booking_request_agent_id"), "booking_request", ["agent_id"], unique=False)
    op.create_index(
        "ix_booking_request_agent_state", "booking_request", ["agent_id", "state", "created_at"], unique=False
    )
    op.create_index(op.f("ix_booking_request_identity_id"), "booking_request", ["identity_id"], unique=False)
    op.create_index(op.f("ix_booking_request_tenant_id"), "booking_request", ["tenant_id"], unique=False)
    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    """Downgrade schema."""
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
    op.drop_index(op.f("ix_booking_request_tenant_id"), table_name="booking_request")
    op.drop_index(op.f("ix_booking_request_identity_id"), table_name="booking_request")
    op.drop_index("ix_booking_request_agent_state", table_name="booking_request")
    op.drop_index(op.f("ix_booking_request_agent_id"), table_name="booking_request")
    op.drop_table("booking_request")
