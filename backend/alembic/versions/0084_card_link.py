"""card_link: short Principal Card links (#438); a platform lookup like ask_handle, no personal data

Revision ID: 0084
Revises: 0083
Create Date: 2026-10-24 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0084"
down_revision: str | Sequence[str] | None = "0083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "card_link",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("card_id", sa.String(length=16), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index(op.f("ix_card_link_agent_id"), "card_link", ["agent_id"], unique=False)
    op.create_index(op.f("ix_card_link_tenant_id"), "card_link", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_card_link_tenant_id"), table_name="card_link")
    op.drop_index(op.f("ix_card_link_agent_id"), table_name="card_link")
    op.drop_table("card_link")
