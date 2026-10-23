"""creator payout ledger: integer minor units, no money movement (#132)

Revision ID: 0076
Revises: 0075
Create Date: 2026-10-24 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0076"
down_revision: str | Sequence[str] | None = "0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("payout_entry",)


def upgrade() -> None:
    op.create_table(
        "payout_entry",
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("creator", sa.String(length=80), nullable=False),
        sa.Column("skill_id", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("receipt_seq", sa.Integer(), nullable=True),
        sa.Column("gross_minor", sa.Integer(), nullable=False),
        sa.Column("fee_minor", sa.Integer(), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "kind", "receipt_seq", name="uq_payout_entry_receipt"),
    )
    for col in ("agent_id", "tenant_id", "creator"):
        op.create_index(op.f(f"ix_payout_entry_{col}"), "payout_entry", [col], unique=False)
    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
    op.drop_table("payout_entry")
