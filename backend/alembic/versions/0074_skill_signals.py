"""skill trust signals: scanner verdicts, allowlisted counters, demotions (#130)

Revision ID: 0074
Revises: 0073
Create Date: 2026-10-24 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0074"
down_revision: str | Sequence[str] | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("skill_scan", "skill_counter", "skill_demotion")


def _scoped() -> list[sa.Column]:
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def _indexes(table: str, *extra: str) -> None:
    for col in ("agent_id", "tenant_id", *extra):
        op.create_index(op.f(f"ix_{table}_{col}"), table, [col], unique=False)


def upgrade() -> None:
    op.create_table(
        "skill_scan",
        sa.Column("skill_id", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("scanner", sa.String(length=40), nullable=False),
        sa.Column("verdict", sa.String(length=12), nullable=False),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("human_concern", sa.Boolean(), nullable=True),
        *_scoped(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "skill_id", "version", "scanner", name="uq_skill_scan"),
    )
    _indexes("skill_scan", "skill_id")
    op.create_table(
        "skill_counter",
        sa.Column("skill_id", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        *_scoped(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "skill_id", "version", "kind", "key", name="uq_skill_counter"),
    )
    _indexes("skill_counter", "skill_id")
    op.create_table(
        "skill_demotion",
        sa.Column("skill_id", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("gaps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("demoted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        *_scoped(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "skill_id", "version", name="uq_skill_demotion"),
    )
    _indexes("skill_demotion", "skill_id")
    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
    for table in reversed(TABLES):
        op.drop_table(table)
