"""identity_claim: unknown accounts named like a known person, waiting for the owner (issue 93)

Revision ID: 0069
Revises: 0068
Create Date: 2026-10-24 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from superaide.db.rls import disable, enable

revision: str = "0069"
down_revision: str | Sequence[str] | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "identity_claim",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("identity_id", sa.UUID(), nullable=False),
        sa.Column("matched_person_id", sa.UUID(), nullable=True),
        sa.Column("claimed_name", sa.String(length=80), nullable=False),
        sa.Column("facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("asks", sa.Integer(), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["identity_id"], ["channel_identity.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matched_person_id"], ["person.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "identity_id", name="uq_identity_claim_identity"),
    )
    op.create_index(op.f("ix_identity_claim_agent_id"), "identity_claim", ["agent_id"])
    op.create_index(op.f("ix_identity_claim_identity_id"), "identity_claim", ["identity_id"])
    op.create_index(op.f("ix_identity_claim_tenant_id"), "identity_claim", ["tenant_id"])
    enable(op.get_bind(), ["identity_claim"])


def downgrade() -> None:
    disable(op.get_bind(), ["identity_claim"])
    op.drop_table("identity_claim")
