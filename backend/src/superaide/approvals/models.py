"""Tables of `approvals/` (ADR 0015 phase 7): moved out of platform/db/models.py unchanged."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class Approval(Scoped, Base):
    __tablename__ = "approval"
    __table_args__ = (Index("ix_approval_agent_created", "agent_id", text("created_at DESC"), text("id DESC")),)

    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    requested_by_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Issue #111: the hash of kind + payload when the request was opened (policy/binding.py), and when the
    # request stops being answerable (empty = never).
    bound_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalEvent(Scoped, Base):
    """One thing that happened to an approval card, as numbers only (#407). Append-only.

    No payload, no summary, no recipient and no name: a kind word (`gmail_send`), what happened, and timings. It
    answers "how much am I being asked, and am I reading it?" without keeping anything the card said.

    event: shown (first listed to the owner) | opened (the owner looked at the card) | decided | brake_held
    """

    __tablename__ = "approval_event"
    __table_args__ = (
        Index("ix_approval_event_agent_created", "agent_id", text("created_at DESC"), text("id DESC")),
        # A card is shown, opened and decided once each; a brake can hold the same card more than once.
        Index(
            "ux_approval_event_once",
            "approval_id",
            "event",
            unique=True,
            postgresql_where=text("event IN ('shown', 'opened', 'decided')"),
        ),
    )

    approval_id: Mapped[uuid.UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(String(40))
    event: Mapped[str] = mapped_column(String(16))
    verdict: Mapped[str | None] = mapped_column(String(8), nullable=True)  # decided: approve | reject | expired
    via: Mapped[str] = mapped_column(String(16), default="")  # decided: app | telegram | slack | ...
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # decided: since the request was made
    opened: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # decided: was the card opened first
    since_opened_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # decided: since it was opened
