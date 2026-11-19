"""visitor_question: a visitor's question Ava could not answer, asked to the owner (#494)

Revision ID: 0096
Revises: 0095
Create Date: 2026-11-19 16:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

TABLES = ("visitor_question",)

# revision identifiers, used by Alembic.
revision: str = "0096"
down_revision: str | Sequence[str] | None = "0095"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "visitor_question",
        sa.Column("identity_id", sa.UUID(), nullable=False),
        sa.Column("context_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("delivery", sa.String(length=10), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["context_id"], ["context.id"]),
        sa.ForeignKeyConstraint(["identity_id"], ["channel_identity.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_visitor_question_agent_id"), "visitor_question", ["agent_id"], unique=False)
    op.create_index(
        "ix_visitor_question_agent_state", "visitor_question", ["agent_id", "state", "created_at"], unique=False
    )
    op.create_index(op.f("ix_visitor_question_identity_id"), "visitor_question", ["identity_id"], unique=False)
    op.create_index(op.f("ix_visitor_question_tenant_id"), "visitor_question", ["tenant_id"], unique=False)
    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    """Downgrade schema."""
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
    op.drop_index(op.f("ix_visitor_question_tenant_id"), table_name="visitor_question")
    op.drop_index(op.f("ix_visitor_question_identity_id"), table_name="visitor_question")
    op.drop_index("ix_visitor_question_agent_state", table_name="visitor_question")
    op.drop_index(op.f("ix_visitor_question_agent_id"), table_name="visitor_question")
    op.drop_table("visitor_question")
