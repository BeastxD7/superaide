"""Triage tables (ADR 0010 section 7). Every table is per agent instance (`Scoped`: tenant_id,
agent_id), so Row-Level Security (db/rls.py) keeps each owner's triage apart.

- `triage_settings`: one row per owner; the ladder, the interrupt budget, thresholds, silenced
  senders and topics, away and busy. A sibling of `owner_preference`, not new columns on it.
- `triage_decision`: one row per inbound message from someone other than the owner: the
  features, every rule that matched, the classifier's checked output and the decision.
- `escalation`, `escalation_delivery`, `escalation_ack`: the ladder for an interrupt.
- `triage_rule`: owner-specific adjustments learned from corrections (TR6).

Audit details hold ids and labels, never message text: the text is in `event`, under its audience.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped
from superaide.triage.types import DEFAULTS


def _now() -> datetime:
    return datetime.now(UTC)


def _ladder() -> list[dict[str, Any]]:
    return [dict(r) for r in DEFAULTS["ladder"]]


class TriageSettings(Scoped, Base):
    """How the owner wants triage and escalation to behave. Created with the proposed defaults."""

    __tablename__ = "triage_settings"

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), unique=True)
    ladder: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=_ladder)
    expire_after_s: Mapped[int] = mapped_column(Integer, default=DEFAULTS["expire_after_s"])
    max_per_hour: Mapped[int] = mapped_column(Integer, default=DEFAULTS["max_per_hour"])
    max_per_day: Mapped[int] = mapped_column(Integer, default=DEFAULTS["max_per_day"])
    vip_ignores_budget: Mapped[str] = mapped_column(String(12), default=DEFAULTS["vip_ignores_budget"])
    theta_interrupt: Mapped[float] = mapped_column(Float, default=DEFAULTS["theta_interrupt"])
    theta_handle: Mapped[float] = mapped_column(Float, default=DEFAULTS["theta_handle"])
    silenced_people: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    silenced_topics: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    autonomy_grants: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)  # answerable intent ids
    stranger_intake: Mapped[bool] = mapped_column(Boolean, default=DEFAULTS["stranger_intake"])
    away_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    away_visibility: Mapped[str] = mapped_column(String(12), default=DEFAULTS["away_visibility"])
    busy_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    phone_enabled: Mapped[bool] = mapped_column(Boolean, default=DEFAULTS["phone_enabled"])
    tell_sender_seen: Mapped[bool] = mapped_column(Boolean, default=DEFAULTS["tell_sender_seen"])
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class TriageDecision(Scoped, Base):
    __tablename__ = "triage_decision"
    __table_args__ = (Index("ix_triage_decision_agent_decision", "agent_id", "decision", "created_at"),)

    inbound_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event.id"), unique=True)
    sender_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    context_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("context.id"))
    # relation, vip, trust, quiet, away, repeats, budget_left, chat_kind, provider, shadow_decision, …
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    rule_hits: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    classifier: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)  # a checked TriageOutput
    classifier_model: Mapped[str] = mapped_column(String(120), default="")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    agreement: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision: Mapped[str] = mapped_column(String(12))  # handle | hold | interrupt | defer | verify
    reason: Mapped[str] = mapped_column(Text, default="")  # owner-only: why, in a line
    stage_at_time: Mapped[str] = mapped_column(String(12))
    policy_version: Mapped[str] = mapped_column(String(40))
    sender_template_id: Mapped[str] = mapped_column(String(40), default="")  # set by TR5
    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approval.id"), nullable=True)
    # useful | not_urgent | should_have_told | wrong_handle | good_handle
    feedback: Mapped[str | None] = mapped_column(String(20), nullable=True)
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    feedback_via: Mapped[str | None] = mapped_column(String(20), nullable=True)  # app | telegram | slack | inferred


class Escalation(Scoped, Base):
    """The ladder for one interrupt: deferred → notifying(step n) → acked → resolved, or expired."""

    __tablename__ = "escalation"
    __table_args__ = (Index("ix_escalation_agent_state", "agent_id", "state", "next_at"),)

    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("triage_decision.id"), unique=True)
    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"))
    state: Mapped[str] = mapped_column(String(12))  # deferred | notifying | acked | resolved | expired
    step: Mapped[int] = mapped_column(Integer, default=0)
    max_step: Mapped[int] = mapped_column(Integer, default=2)  # the stage's cap on rungs
    breaks_quiet: Mapped[bool] = mapped_column(Boolean, default=False)  # a VIP who may break quiet hours
    next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_via: Mapped[str | None] = mapped_column(String(20), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(12), nullable=True)  # replied | done | dismissed | expired
    cycle: Mapped[int] = mapped_column(Integer, default=0)


class EscalationDelivery(Scoped, Base):
    """One rung sent (or tried) on one channel. Written before the send, so a retry knows."""

    __tablename__ = "escalation_delivery"
    __table_args__ = (
        UniqueConstraint("escalation_id", "cycle", "step", "provider", name="uq_escalation_delivery_step"),
    )

    escalation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("escalation.id"), index=True)
    step: Mapped[int] = mapped_column(Integer)
    cycle: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # a snooze starts a new cycle
    provider: Mapped[str] = mapped_column(String(20))  # app | telegram | slack
    # For buttons (TR4): an opaque token in callback_data / value, never the escalation id.
    token: Mapped[str] = mapped_column(String(24), unique=True)
    external_ref: Mapped[str] = mapped_column(String(200), default="")  # chat_id:message_id | channel:ts
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | sent | failed | skipped
    error: Mapped[str] = mapped_column(Text, default="")


class EscalationAck(Scoped, Base):
    """An explicit acknowledgement. A duplicate press hits the unique index and changes nothing."""

    __tablename__ = "escalation_ack"
    __table_args__ = (UniqueConstraint("escalation_id", "source", "external_id", name="uq_escalation_ack_source"),)

    escalation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("escalation.id"), index=True)
    source: Mapped[str] = mapped_column(String(12))  # telegram | slack | app | phone
    external_id: Mapped[str] = mapped_column(String(120))  # callback_query.id | action_ts | app request id
    verb: Mapped[str] = mapped_column(String(20))
    actor_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TriageRule(Scoped, Base):
    """An owner-specific adjustment learned from a correction; the owner can see and remove each one."""

    __tablename__ = "triage_rule"

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    # sender_floor | sender_ceiling | topic_add | topic_mute | threshold_delta
    kind: Mapped[str] = mapped_column(String(20))
    target: Mapped[str] = mapped_column(String(200), default="")  # a person id, a topic, or ""
    value: Mapped[str] = mapped_column(String(60), default="")
    source_decision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("triage_decision.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TriageLabelVector(Scoped, Base):
    """A cached embedding of the message behind one owner label (#117), so the kNN head doesn't re-embed
    the owner's history on every message. It is derived data: the label is `triage_decision.feedback`,
    and the head reads a vector only while that feedback is still set by the owner, so clearing the
    feedback removes the label's influence at once (and the row is deleted with it). The row goes with
    its decision (ON DELETE CASCADE)."""

    __tablename__ = "triage_label_vector"
    __table_args__ = (UniqueConstraint("decision_id", "model", name="uq_triage_label_vector"),)

    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("triage_decision.id", ondelete="CASCADE"), index=True)
    model: Mapped[str] = mapped_column(String(160))  # the embedder; another embedder means new rows
    embedding: Mapped[list[float]] = mapped_column(JSONB)
