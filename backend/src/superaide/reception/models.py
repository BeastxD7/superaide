"""Receptionist tables. Both are per agent instance (`Scoped`), under Row-Level Security.

- `reception_settings`: one row per owner: on or off, whether strangers may be told the owner's
  first name, the owner's public profile text, and the per-sender limits.
- `reception_message`: a message Ava took from someone she doesn't know. What they typed is data:
  shown to the owner in the app, never put in the owner's chat or a push, never in a model prompt
  except that same stranger's own conversation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from superaide.db.models import Base, Scoped

PER_HOUR = 10  # messages from one unknown sender before Ava stops chatting for the hour
PER_DAY = 30


def _now() -> datetime:
    return datetime.now(UTC)


class ReceptionSettings(Scoped, Base):
    __tablename__ = "reception_settings"

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    show_first_name: Mapped[bool] = mapped_column(Boolean, default=False)  # "Hi, I'm Ava, Shashank's AI assistant"
    # Written by the owner for anyone: what they do, when they're reachable, the best way to reach them.
    public_profile: Mapped[str] = mapped_column(Text, default="")
    per_hour: Mapped[int] = mapped_column(Integer, default=PER_HOUR)
    per_day: Mapped[int] = mapped_column(Integer, default=PER_DAY)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ReceptionMessage(Scoped, Base):
    __tablename__ = "reception_message"
    __table_args__ = (Index("ix_reception_message_agent_status", "agent_id", "status", "created_at"),)

    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channel_identity.id"), index=True)
    context_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("context.id"))
    inbound_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("event.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(20))  # telegram | slack
    handle: Mapped[str] = mapped_column(String(200), default="")  # @username / display name: display only
    # What they said about themselves and why they wrote. Unverified, every word of it.
    name: Mapped[str] = mapped_column(String(120), default="")
    contact: Mapped[str] = mapped_column(String(200), default="")
    topic: Mapped[str] = mapped_column(String(200), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    deadline_text: Mapped[str] = mapped_column(String(120), default="")
    claimed_urgency: Mapped[str] = mapped_column(String(10), default="normal")  # their claim, never evidence
    rank: Mapped[str] = mapped_column(String(12), default="normal")  # unverified | urgent | normal | low (code)
    rule_hits: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    source: Mapped[str] = mapped_column(String(10), default="model")  # model | code (no model, or over budget)
    status: Mapped[str] = mapped_column(String(12), default="new")  # new | done | dismissed | spam
    decision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("triage_decision.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
