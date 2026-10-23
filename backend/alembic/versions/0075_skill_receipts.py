"""skill receipt log: append-only hash-chained outcome receipts (#131)

Revision ID: 0075
Revises: 0074
Create Date: 2026-10-24 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0075"
down_revision: str | Sequence[str] | None = "0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("skill_receipt",)


def upgrade() -> None:
    op.create_table(
        "skill_receipt",
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.Column("skill_id", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("effect_class", sa.String(length=20), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("owner_ref", sa.String(length=32), nullable=False),
        sa.Column("ref_seq", sa.Integer(), nullable=True),
        sa.Column("latency_s", sa.Integer(), nullable=False),
        sa.Column("batch", sa.Boolean(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "seq", name="uq_skill_receipt_seq"),
    )
    for col in ("agent_id", "tenant_id", "skill_id"):
        op.create_index(op.f(f"ix_skill_receipt_{col}"), "skill_receipt", [col], unique=False)
    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
    op.drop_table("skill_receipt")
