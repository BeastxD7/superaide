"""triage: decisions, escalations and owner settings (ADR 0010 TR0)

Six per-instance tables for triage and escalation, each under the Row-Level Security policy
(ADR 0009): triage_settings, triage_decision, escalation, escalation_delivery, escalation_ack,
triage_rule. Nothing reads or writes them unless TRIAGE_ENABLED is on.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-22 05:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("triage_settings", "triage_decision", "escalation", "escalation_delivery", "escalation_ack", "triage_rule")


def upgrade() -> None:
    op.create_table(
        "triage_settings",
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("ladder", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expire_after_s", sa.Integer(), nullable=False),
        sa.Column("max_per_hour", sa.Integer(), nullable=False),
        sa.Column("max_per_day", sa.Integer(), nullable=False),
        sa.Column("vip_ignores_budget", sa.String(length=12), nullable=False),
        sa.Column("theta_interrupt", sa.Float(), nullable=False),
        sa.Column("theta_handle", sa.Float(), nullable=False),
        sa.Column("silenced_people", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("silenced_topics", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("autonomy_grants", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("stranger_intake", sa.Boolean(), nullable=False),
        sa.Column("away_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("away_visibility", sa.String(length=12), nullable=False),
        sa.Column("busy_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("phone_enabled", sa.Boolean(), nullable=False),
        sa.Column("tell_sender_seen", sa.Boolean(), nullable=False),
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
    op.create_index(op.f("ix_triage_settings_agent_id"), "triage_settings", ["agent_id"], unique=False)
    op.create_index(op.f("ix_triage_settings_tenant_id"), "triage_settings", ["tenant_id"], unique=False)
    op.create_table(
        "triage_decision",
        sa.Column("inbound_event_id", sa.UUID(), nullable=False),
        sa.Column("sender_person_id", sa.UUID(), nullable=True),
        sa.Column("context_id", sa.UUID(), nullable=False),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rule_hits", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("classifier", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("classifier_model", sa.String(length=120), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("agreement", sa.Float(), nullable=True),
        sa.Column("decision", sa.String(length=12), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("stage_at_time", sa.String(length=12), nullable=False),
        sa.Column("policy_version", sa.String(length=40), nullable=False),
        sa.Column("sender_template_id", sa.String(length=40), nullable=False),
        sa.Column("approval_id", sa.UUID(), nullable=True),
        sa.Column("feedback", sa.String(length=20), nullable=True),
        sa.Column("feedback_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("feedback_via", sa.String(length=20), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approval.id"],
        ),
        sa.ForeignKeyConstraint(
            ["context_id"],
            ["context.id"],
        ),
        sa.ForeignKeyConstraint(
            ["inbound_event_id"],
            ["event.id"],
        ),
        sa.ForeignKeyConstraint(
            ["sender_person_id"],
            ["person.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("inbound_event_id"),
    )
    op.create_index(
        "ix_triage_decision_agent_decision", "triage_decision", ["agent_id", "decision", "created_at"], unique=False
    )
    op.create_index(op.f("ix_triage_decision_agent_id"), "triage_decision", ["agent_id"], unique=False)
    op.create_index(op.f("ix_triage_decision_tenant_id"), "triage_decision", ["tenant_id"], unique=False)
    op.create_table(
        "escalation",
        sa.Column("decision_id", sa.UUID(), nullable=False),
        sa.Column("owner_person_id", sa.UUID(), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("max_step", sa.Integer(), nullable=False),
        sa.Column("breaks_quiet", sa.Boolean(), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acked_via", sa.String(length=20), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(length=12), nullable=True),
        sa.Column("cycle", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["triage_decision.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_person_id"],
            ["person.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id"),
    )
    op.create_index(op.f("ix_escalation_agent_id"), "escalation", ["agent_id"], unique=False)
    op.create_index("ix_escalation_agent_state", "escalation", ["agent_id", "state", "next_at"], unique=False)
    op.create_index(op.f("ix_escalation_tenant_id"), "escalation", ["tenant_id"], unique=False)
    op.create_table(
        "triage_rule",
        sa.Column("owner_person_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("target", sa.String(length=200), nullable=False),
        sa.Column("value", sa.String(length=60), nullable=False),
        sa.Column("source_decision_id", sa.UUID(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_person_id"],
            ["person.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_decision_id"],
            ["triage_decision.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_triage_rule_agent_id"), "triage_rule", ["agent_id"], unique=False)
    op.create_index(op.f("ix_triage_rule_owner_person_id"), "triage_rule", ["owner_person_id"], unique=False)
    op.create_index(op.f("ix_triage_rule_tenant_id"), "triage_rule", ["tenant_id"], unique=False)
    op.create_table(
        "escalation_ack",
        sa.Column("escalation_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=12), nullable=False),
        sa.Column("external_id", sa.String(length=120), nullable=False),
        sa.Column("verb", sa.String(length=20), nullable=False),
        sa.Column("actor_person_id", sa.UUID(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_person_id"],
            ["person.id"],
        ),
        sa.ForeignKeyConstraint(
            ["escalation_id"],
            ["escalation.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("escalation_id", "source", "external_id", name="uq_escalation_ack_source"),
    )
    op.create_index(op.f("ix_escalation_ack_agent_id"), "escalation_ack", ["agent_id"], unique=False)
    op.create_index(op.f("ix_escalation_ack_escalation_id"), "escalation_ack", ["escalation_id"], unique=False)
    op.create_index(op.f("ix_escalation_ack_tenant_id"), "escalation_ack", ["tenant_id"], unique=False)
    op.create_table(
        "escalation_delivery",
        sa.Column("escalation_id", sa.UUID(), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("token", sa.String(length=24), nullable=False),
        sa.Column("external_ref", sa.String(length=200), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["escalation_id"],
            ["escalation.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("escalation_id", "step", "provider", name="uq_escalation_delivery_step"),
        sa.UniqueConstraint("token"),
    )
    op.create_index(op.f("ix_escalation_delivery_agent_id"), "escalation_delivery", ["agent_id"], unique=False)
    op.create_index(
        op.f("ix_escalation_delivery_escalation_id"), "escalation_delivery", ["escalation_id"], unique=False
    )
    op.create_index(op.f("ix_escalation_delivery_tenant_id"), "escalation_delivery", ["tenant_id"], unique=False)

    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
    op.drop_index(op.f("ix_escalation_delivery_tenant_id"), table_name="escalation_delivery")
    op.drop_index(op.f("ix_escalation_delivery_escalation_id"), table_name="escalation_delivery")
    op.drop_index(op.f("ix_escalation_delivery_agent_id"), table_name="escalation_delivery")
    op.drop_table("escalation_delivery")
    op.drop_index(op.f("ix_escalation_ack_tenant_id"), table_name="escalation_ack")
    op.drop_index(op.f("ix_escalation_ack_escalation_id"), table_name="escalation_ack")
    op.drop_index(op.f("ix_escalation_ack_agent_id"), table_name="escalation_ack")
    op.drop_table("escalation_ack")
    op.drop_index(op.f("ix_triage_rule_tenant_id"), table_name="triage_rule")
    op.drop_index(op.f("ix_triage_rule_owner_person_id"), table_name="triage_rule")
    op.drop_index(op.f("ix_triage_rule_agent_id"), table_name="triage_rule")
    op.drop_table("triage_rule")
    op.drop_index(op.f("ix_escalation_tenant_id"), table_name="escalation")
    op.drop_index("ix_escalation_agent_state", table_name="escalation")
    op.drop_index(op.f("ix_escalation_agent_id"), table_name="escalation")
    op.drop_table("escalation")
    op.drop_index(op.f("ix_triage_decision_tenant_id"), table_name="triage_decision")
    op.drop_index(op.f("ix_triage_decision_agent_id"), table_name="triage_decision")
    op.drop_index("ix_triage_decision_agent_decision", table_name="triage_decision")
    op.drop_table("triage_decision")
    op.drop_index(op.f("ix_triage_settings_tenant_id"), table_name="triage_settings")
    op.drop_index(op.f("ix_triage_settings_agent_id"), table_name="triage_settings")
    op.drop_table("triage_settings")
