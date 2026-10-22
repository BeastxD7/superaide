"""one good question: the daily question and the topics never to ask about (issue 121)

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-23 10:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0034"
down_revision: str | Sequence[str] | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("owner_question", "never_ask_topic")


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "owner_question",
        *_base(),
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("asked_on", sa.Date(), nullable=False),
        sa.Column("topic", sa.String(length=40), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("memory_id", sa.UUID(), nullable=True),
        sa.Column("selected_by", sa.String(length=20), nullable=False),
        sa.CheckConstraint("status in ('asked', 'answered', 'skipped')", name="ck_owner_question_status"),
        sa.ForeignKeyConstraint(["memory_id"], ["memory_item.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["person_id"], ["person.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("person_id", "asked_on", name="uq_owner_question_day"),
    )
    op.create_table(
        "never_ask_topic",
        *_base(),
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("topic", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["person.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("person_id", "topic", name="uq_never_ask_topic"),
    )
    for t in TABLES:
        op.create_index(op.f(f"ix_{t}_agent_id"), t, ["agent_id"], unique=False)
        op.create_index(op.f(f"ix_{t}_tenant_id"), t, ["tenant_id"], unique=False)
        op.create_index(op.f(f"ix_{t}_person_id"), t, ["person_id"], unique=False)

    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
    op.drop_table("never_ask_topic")
    op.drop_table("owner_question")
