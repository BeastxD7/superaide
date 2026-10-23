"""update previews: what a skill, prompt or model change would do differently (issue 110)

Revision ID: 0070
Revises: 0069
Create Date: 2026-10-23 18:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0070"
down_revision: str | Sequence[str] | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "update_preview",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("owner_person_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tasks_asked", sa.Integer(), nullable=False),
        sa.Column("tasks_done", sa.Integer(), nullable=False),
        sa.Column("changed", sa.Integer(), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stopped", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("decided_by_person_id", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind in ('skill', 'prompt', 'model')", name="ck_update_preview_kind"),
        sa.CheckConstraint("status in ('pending', 'approved', 'rejected')", name="ck_update_preview_status"),
        sa.ForeignKeyConstraint(["decided_by_person_id"], ["person.id"]),
        sa.ForeignKeyConstraint(["owner_person_id"], ["person.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for col in ("agent_id", "tenant_id", "owner_person_id"):
        op.create_index(op.f(f"ix_update_preview_{col}"), "update_preview", [col], unique=False)

    from superaide.db.rls import enable

    enable(op.get_bind(), ("update_preview",))


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), ("update_preview",))
    op.drop_table("update_preview")
