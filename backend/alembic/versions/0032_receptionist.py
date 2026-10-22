"""receptionist: talk with people Ava doesn't know, on the owner's behalf (ADR 0010 TR5)

Two per-instance tables under Row-Level Security: reception_settings (on by default; the owner's
public profile; whether their first name may be shown) and reception_message (messages taken).

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-22 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("reception_settings", "reception_message")


def upgrade() -> None:
    op.create_table(
        "reception_settings",
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("show_first_name", sa.Boolean(), nullable=False),
        sa.Column("public_profile", sa.Text(), nullable=False),
        sa.Column("per_hour", sa.Integer(), nullable=False),
        sa.Column("per_day", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("person_id"),
    )
    op.create_index(op.f("ix_reception_settings_agent_id"), "reception_settings", ["agent_id"], unique=False)
    op.create_index(op.f("ix_reception_settings_tenant_id"), "reception_settings", ["tenant_id"], unique=False)
    op.create_table(
        "reception_message",
        sa.Column("identity_id", sa.UUID(), nullable=False),
        sa.Column("context_id", sa.UUID(), nullable=False),
        sa.Column("inbound_event_id", sa.UUID(), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("handle", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("contact", sa.String(length=200), nullable=False),
        sa.Column("topic", sa.String(length=200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("deadline_text", sa.String(length=120), nullable=False),
        sa.Column("claimed_urgency", sa.String(length=10), nullable=False),
        sa.Column("rank", sa.String(length=12), nullable=False),
        sa.Column("rule_hits", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("decision_id", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["context_id"],
            ["context.id"],
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["triage_decision.id"],
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["channel_identity.id"],
        ),
        sa.ForeignKeyConstraint(
            ["inbound_event_id"],
            ["event.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_reception_message_agent_id"), "reception_message", ["agent_id"], unique=False)
    op.create_index(
        "ix_reception_message_agent_status", "reception_message", ["agent_id", "status", "created_at"], unique=False
    )
    op.create_index(op.f("ix_reception_message_identity_id"), "reception_message", ["identity_id"], unique=False)
    op.create_index(op.f("ix_reception_message_tenant_id"), "reception_message", ["tenant_id"], unique=False)

    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
    op.drop_index(op.f("ix_reception_message_tenant_id"), table_name="reception_message")
    op.drop_index(op.f("ix_reception_message_identity_id"), table_name="reception_message")
    op.drop_index("ix_reception_message_agent_status", table_name="reception_message")
    op.drop_index(op.f("ix_reception_message_agent_id"), table_name="reception_message")
    op.drop_table("reception_message")
    op.drop_index(op.f("ix_reception_settings_tenant_id"), table_name="reception_settings")
    op.drop_index(op.f("ix_reception_settings_agent_id"), table_name="reception_settings")
    op.drop_table("reception_settings")
