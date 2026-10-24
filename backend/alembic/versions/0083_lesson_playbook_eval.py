"""lesson: owner playbook text, the eval that guards it and the in-prompt switch (#57)

Revision ID: 0083
Revises: 0082
Create Date: 2026-10-24 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0083"
down_revision: str | Sequence[str] | None = "0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lesson", sa.Column("playbook_text", sa.Text(), server_default="", nullable=False))
    op.add_column("lesson", sa.Column("eval_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("lesson", sa.Column("eval_status", sa.String(length=10), server_default="none", nullable=False))
    op.add_column("lesson", sa.Column("eval_hash", sa.String(length=64), server_default="", nullable=False))
    op.add_column("lesson", sa.Column("eval_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("lesson", sa.Column("eval_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("lesson", sa.Column("in_prompt", sa.Boolean(), server_default="false", nullable=False))
    op.create_check_constraint(
        "ck_lesson_prompt_eval", "lesson", "NOT in_prompt OR (eval_status = 'passed' AND status = 'active')"
    )
    op.create_check_constraint("ck_lesson_eval_status", "lesson", "eval_status in ('none', 'passed', 'failed')")


def downgrade() -> None:
    op.drop_constraint("ck_lesson_eval_status", "lesson", type_="check")
    op.drop_constraint("ck_lesson_prompt_eval", "lesson", type_="check")
    for col in ("in_prompt", "eval_at", "eval_report", "eval_hash", "eval_status", "eval_spec", "playbook_text"):
        op.drop_column("lesson", col)
