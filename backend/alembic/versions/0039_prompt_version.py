"""prompt versions: triage prompts the Overnight Tuner proposed and the owner decided on (issue 118)

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-23 16:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0039"
down_revision: str | Sequence[str] | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prompt_version",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("parent_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("base_score", sa.Float(), nullable=True),
        sa.Column("cand_score", sa.Float(), nullable=True),
        sa.Column("held_out", sa.Integer(), nullable=False),
        sa.Column("invariants", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("decided_by_person_id", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["decided_by_person_id"], ["person.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "key", "version", name="uq_prompt_version"),
    )
    op.create_index(op.f("ix_prompt_version_agent_id"), "prompt_version", ["agent_id"], unique=False)
    op.create_index(op.f("ix_prompt_version_tenant_id"), "prompt_version", ["tenant_id"], unique=False)

    from superaide.db.rls import enable

    enable(op.get_bind(), ("prompt_version",))


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), ("prompt_version",))
    op.drop_table("prompt_version")
