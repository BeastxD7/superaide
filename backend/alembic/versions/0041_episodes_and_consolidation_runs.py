"""episode and consolidation_run: the sleep job's output and its restorable pre-images (#50)

Two new per-instance tables under Row-Level Security. Nothing writes them unless SLEEP_JOB_ENABLED.

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-23 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0041"
down_revision: str | Sequence[str] | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "episode",
        sa.Column("owner_person_id", sa.UUID(), nullable=False),
        sa.Column("context_id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("participant_person_ids", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("audience_person_ids", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("audience_has_unknown", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_ids", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("summary_by", sa.String(length=20), nullable=False),
        sa.Column("hints", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        *_base(),
        sa.ForeignKeyConstraint(["owner_person_id"], ["person.id"]),
        sa.ForeignKeyConstraint(["context_id"], ["context.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "consolidation_run",
        sa.Column("owner_person_id", sa.UUID(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.Column("episodes_made", sa.Integer(), nullable=False),
        sa.Column("entries_before", sa.Integer(), nullable=False),
        sa.Column("retired_ids", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("notes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        *_base(),
        sa.ForeignKeyConstraint(["owner_person_id"], ["person.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for table in ("episode", "consolidation_run"):
        for col in ("agent_id", "tenant_id", "owner_person_id"):
            op.create_index(op.f(f"ix_{table}_{col}"), table, [col], unique=False)
    op.create_index(op.f("ix_episode_context_id"), "episode", ["context_id"], unique=False)
    from superaide.db.rls import enable

    enable(op.get_bind(), ["episode", "consolidation_run"])


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), ["episode", "consolidation_run"])
    op.drop_table("consolidation_run")
    op.drop_table("episode")
