"""spend ledger provider column and the owner's model prices

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-22 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every call so far went to Anthropic.
    op.add_column(
        "spend_ledger",
        sa.Column("provider", sa.String(length=40), server_default="anthropic", nullable=False),
    )
    op.create_table(
        "owner_model_price",
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("input_usd_per_mtok", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("output_usd_per_mtok", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("cache_read_mult", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("set_by_user_id", sa.UUID(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["set_by_user_id"], ["user_account.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "provider", "model", name="uq_owner_model_price"),
    )
    op.create_index(op.f("ix_owner_model_price_agent_id"), "owner_model_price", ["agent_id"], unique=False)
    op.create_index(op.f("ix_owner_model_price_tenant_id"), "owner_model_price", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_owner_model_price_tenant_id"), table_name="owner_model_price")
    op.drop_index(op.f("ix_owner_model_price_agent_id"), table_name="owner_model_price")
    op.drop_table("owner_model_price")
    op.drop_column("spend_ledger", "provider")
