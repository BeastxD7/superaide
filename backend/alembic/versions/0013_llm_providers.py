"""model providers and model roles per Ava

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-22 18:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scoped() -> list[sa.Column]:
    return [
        sa.Column("set_by_user_id", sa.UUID(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "llm_provider",
        sa.Column("preset", sa.String(length=20), nullable=False),
        sa.Column("base_url", sa.String(length=300), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_scoped(),
        sa.ForeignKeyConstraint(["set_by_user_id"], ["user_account.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "preset", name="uq_llm_provider"),
    )
    op.create_index(op.f("ix_llm_provider_agent_id"), "llm_provider", ["agent_id"], unique=False)
    op.create_index(op.f("ix_llm_provider_tenant_id"), "llm_provider", ["tenant_id"], unique=False)
    op.create_table(
        "model_role",
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        *_scoped(),
        sa.ForeignKeyConstraint(["set_by_user_id"], ["user_account.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "role", name="uq_model_role"),
    )
    op.create_index(op.f("ix_model_role_agent_id"), "model_role", ["agent_id"], unique=False)
    op.create_index(op.f("ix_model_role_tenant_id"), "model_role", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_model_role_tenant_id"), table_name="model_role")
    op.drop_index(op.f("ix_model_role_agent_id"), table_name="model_role")
    op.drop_table("model_role")
    op.drop_index(op.f("ix_llm_provider_tenant_id"), table_name="llm_provider")
    op.drop_index(op.f("ix_llm_provider_agent_id"), table_name="llm_provider")
    op.drop_table("llm_provider")
