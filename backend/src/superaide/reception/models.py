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

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped

PER_HOUR = 10  # messages from one unknown sender before Ava stops chatting for the hour
PER_DAY = 30


def _now() -> datetime:
    return datetime.now(UTC)


class ReceptionSettings(Scoped, Base):
    __tablename__ = "reception_settings"

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    show_first_name: Mapped[bool] = mapped_column(Boolean, default=False)  # "Hi, I'm Ava, Jordan's AI assistant"
    # Written by the owner for anyone: what they do, when they're reachable, the best way to reach them.
    public_profile: Mapped[str] = mapped_column(Text, default="")
    per_hour: Mapped[int] = mapped_column(Integer, default=PER_HOUR)
    per_day: Mapped[int] = mapped_column(Integer, default=PER_DAY)
    # Owner opt-in (#477): a message that looks urgent may still notify after the sender's hourly cap of
    # notifications is used up. Off: urgency never lifts the cap.
    urgent_over_cap: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Live takeover (#493): minutes without a message from the owner after which Ava takes a conversation back.
    # NULL: the deployment default (`TAKEOVER_IDLE_MINUTES`, 150).
    takeover_idle_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Takeover(Scoped, Base):
    """The owner is handling a visitor's conversation themselves (#493). While `status` is `active`, code stops Ava from
    replying in `context_id`; what the visitor writes is still stored, and what the owner writes is kept as context.
    Ava takes the conversation back when the owner says so (`released`, reason `owner` or `command`) or after the owner
    has been silent for the idle window (reason `idle`). At most one active row per conversation."""

    __tablename__ = "takeover"
    __table_args__ = (
        Index("ix_takeover_agent_status", "agent_id", "status", "last_owner_message_at"),
        Index("uq_takeover_active_context", "context_id", unique=True, postgresql_where=text("status = 'active'")),
    )

    context_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("context.id"))
    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"))
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | released
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_owner_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_reason: Mapped[str] = mapped_column(String(10), default="")  # "" | idle | owner | command
    # When the owner was last told the visitor wrote again (at most once in `takeover.PING_EVERY`).
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VisitorQuestion(Scoped, Base):
    """Something a visitor asked that Ava could not answer from the public information (#494). `question` is
    written by the visitor, so it is data: shown quoted to the owner, never put in a notification or in the owner's
    chat. `answer` is the owner's own typed text; code relays it inside a fixed template."""

    __tablename__ = "visitor_question"
    __table_args__ = (Index("ix_visitor_question_agent_state", "agent_id", "state", "created_at"),)

    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channel_identity.id"), index=True)
    context_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("context.id"))
    provider: Mapped[str] = mapped_column(String(20))  # telegram | slack
    question: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(10), default="asked")  # asked | answered | declined | expired
    answer: Mapped[str] = mapped_column(Text, default="")
    delivery: Mapped[str] = mapped_column(String(10), default="")  # "" | sent | failed (the relay to the visitor)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BookingRequest(Scoped, Base):
    """A visitor's request for time with the owner (#496). Slots are chosen by code from the owner's real free/busy and
    stored here; the visitor picks one by number, never by typing a time. Nothing is written to the owner's calendar
    until the owner approves the card (`approval_id`): `pending` is only a hold in this table that keeps the same time
    from being offered twice.

    state: proposed (times offered) | pending (visitor chose, waiting for the owner) | approved (event created) |
    declined | expired | failed (approved, but the calendar refused or the time was taken)"""

    __tablename__ = "booking_request"
    __table_args__ = (Index("ix_booking_request_agent_state", "agent_id", "state", "created_at"),)

    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channel_identity.id"), index=True)
    context_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("context.id"))
    provider: Mapped[str] = mapped_column(String(20))  # telegram | slack
    minutes: Mapped[int] = mapped_column(Integer, default=30)
    # The zone times are shown to the visitor in: an IANA name they stated and code validated, else UTC.
    tz_name: Mapped[str] = mapped_column(String(64), default="UTC")
    visitor_name: Mapped[str] = mapped_column(String(40), default="")  # their claim, letters only, display only
    visitor_email: Mapped[str] = mapped_column(String(254), default="")  # validated syntax; unverified
    slots: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)  # [{"start": ISO UTC, "end": ISO UTC}]
    chosen: Mapped[int | None] = mapped_column(Integer, nullable=True)  # index into slots
    state: Mapped[str] = mapped_column(String(10), default="proposed")
    approval_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    event_id: Mapped[str] = mapped_column(String(1024), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReceptionMessage(Scoped, Base):
    __tablename__ = "reception_message"
    __table_args__ = (
        Index("ix_reception_message_agent_status", "agent_id", "status", "created_at"),
        # Recall by time and by sender, and the words of a message (#644). The expression is the one recall searches.
        Index("ix_reception_message_agent_created", "agent_id", "created_at"),
        Index(
            "ix_reception_message_fts",
            text("to_tsvector('english', message)"),
            postgresql_using="gin",
        ),
    )

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
    # The moment `deadline_text` names, read in code in the owner's time zone and anchored on when the message
    # arrived. NULL with deadline_parse "unknown" (no time found: the owner reads the words) or "past".
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_parse: Mapped[str] = mapped_column(String(10), default="")  # "" (none given) | ok | unknown | past
    claimed_urgency: Mapped[str] = mapped_column(String(10), default="normal")  # their claim, never evidence
    rank: Mapped[str] = mapped_column(String(12), default="normal")  # unverified | urgent | normal | low (code)
    rule_hits: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    source: Mapped[str] = mapped_column(String(10), default="model")  # model | code (no model, or over budget)
    status: Mapped[str] = mapped_column(String(12), default="new")  # new | done | dismissed | spam
    decision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("triage_decision.id"), nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # owner was told
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
