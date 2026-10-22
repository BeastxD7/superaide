"""Tables for the "Ask my Ava" link.

- `ask_handle`: the platform-wide lookup from a public handle to the instance that owns it. It holds
  no content (no names, no requests), and it is read before any instance is in scope, so it is exempt
  from Row-Level Security like `channel_endpoint` (db/rls.py EXEMPT). A handle an owner changed or
  revoked stays here, unpublished, so nobody else can take over a link that is still in old email
  signatures.
- `ask_link`: the owner's link inside their instance: the handle, whether it's published, and what the
  page may show (the owner's first name only when they opt in).
- `ask_request`: one request a visitor left, with the rank code gave it. Under Row-Level Security.
- `ask_stat`: growth counts per instance per day (views, requests, dropped, "Get your own" clicks).
  Numbers only: no addresses, no names.
- `ask_rate`: short-lived counters for the public form's rate limits. Keys are a keyed hash of the
  visitor's address (never the address itself) or a handle id, plus the hour.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from superaide.db.models import Base, Scoped


def _now() -> datetime:
    return datetime.now(UTC)


class AskHandle(Base):
    __tablename__ = "ask_handle"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    handle: Mapped[str] = mapped_column(String(40), unique=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_instance.id"), index=True)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())


class AskLink(Scoped, Base):
    __tablename__ = "ask_link"
    __table_args__ = (
        UniqueConstraint("agent_id", "handle", name="uq_ask_link_handle"),
        UniqueConstraint("agent_id", "owner_person_id", name="uq_ask_link_owner"),
    )

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"))
    handle: Mapped[str] = mapped_column(String(40))
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    show_first_name: Mapped[bool] = mapped_column(Boolean, default=False)  # "Shashank's assistant"
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AskRequest(Scoped, Base):
    __tablename__ = "ask_request"
    __table_args__ = (Index("ix_ask_request_agent_status", "agent_id", "status", "created_at"),)

    link_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ask_link.id"), index=True)
    # What the visitor typed. Data only: never put in a model prompt for the visitor.
    name: Mapped[str] = mapped_column(String(80))
    contact: Mapped[str] = mapped_column(String(200))  # validated, never verified, never used without approval
    contact_kind: Mapped[str] = mapped_column(String(10))  # email | phone
    topic: Mapped[str] = mapped_column(String(140))
    why: Mapped[str] = mapped_column(Text, default="")
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Code's rank: unverified (risk screen) | urgent (urgent topic or a near deadline) | normal | low (silenced)
    rank: Mapped[str] = mapped_column(String(12))
    rule_hits: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    status: Mapped[str] = mapped_column(String(12), default="new")  # new | replied | dismissed | spam
    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approval.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AskStat(Scoped, Base):
    __tablename__ = "ask_stat"
    __table_args__ = (UniqueConstraint("agent_id", "day", name="uq_ask_stat_day"),)

    day: Mapped[date] = mapped_column(Date)
    views: Mapped[int] = mapped_column(Integer, default=0)
    submissions: Mapped[int] = mapped_column(Integer, default=0)
    dropped: Mapped[int] = mapped_column(Integer, default=0)  # honeypot or too fast
    get_own_clicks: Mapped[int] = mapped_column(Integer, default=0)


class AskRate(Base):
    __tablename__ = "ask_rate"

    bucket: Mapped[str] = mapped_column(String(120), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    hits: Mapped[int] = mapped_column(Integer, default=0)
