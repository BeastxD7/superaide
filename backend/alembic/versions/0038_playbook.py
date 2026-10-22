"""playbooks: drafted by Ava, live only when the owner edited and approved them (issue 120)

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-23 15:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0038"
down_revision: str | Sequence[str] | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "playbook",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("owner_person_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("drafted_body", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("tools", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("approved_by_person_id", sa.UUID(), nullable=True),
        sa.Column("retired_reason", sa.String(length=200), nullable=False),
        sa.Column("used_n", sa.Integer(), nullable=False),
        sa.Column("used_dist", sa.Float(), nullable=False),
        sa.Column("unused_n", sa.Integer(), nullable=False),
        sa.Column("unused_dist", sa.Float(), nullable=False),
        sa.CheckConstraint("status in ('draft', 'active', 'retired')", name="ck_playbook_status"),
        sa.CheckConstraint(
            "status <> 'active' OR approved_by_person_id IS NOT NULL", name="ck_playbook_active_approved"
        ),
        sa.ForeignKeyConstraint(["approved_by_person_id"], ["person.id"]),
        sa.ForeignKeyConstraint(["owner_person_id"], ["person.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for col in ("agent_id", "tenant_id", "owner_person_id"):
        op.create_index(op.f(f"ix_playbook_{col}"), "playbook", [col], unique=False)
    op.execute(
        """
CREATE OR REPLACE FUNCTION playbook_active_cap() RETURNS trigger AS $$
BEGIN
  IF NEW.status = 'active' AND (SELECT count(*) FROM playbook
       WHERE agent_id = NEW.agent_id AND status = 'active' AND id <> NEW.id) >= 50 THEN
    RAISE EXCEPTION 'at most 50 active playbooks';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER playbook_active_cap BEFORE INSERT OR UPDATE ON playbook
  FOR EACH ROW EXECUTE FUNCTION playbook_active_cap();
"""
    )

    from superaide.db.rls import enable

    enable(op.get_bind(), ("playbook",))


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), ("playbook",))
    op.execute("DROP TRIGGER IF EXISTS playbook_active_cap ON playbook")
    op.execute("DROP FUNCTION IF EXISTS playbook_active_cap()")
    op.drop_table("playbook")
