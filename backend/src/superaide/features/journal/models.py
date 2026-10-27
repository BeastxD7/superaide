"""Tables of `features/journal/` (ADR 0015 phase 7): moved out of platform/db/models.py unchanged."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class JournalSetting(Scoped, Base):
    """Whether this owner keeps a decision journal ("Ava keeps score"). Off until the owner turns it on."""

    __tablename__ = "journal_setting"

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Prediction(Scoped, Base):
    """Something the owner said they expect, with how sure they were, so it can be checked later.

    Always the owner's own words (`quote` is verified against their message in code). Private to the owner;
    scoring is arithmetic over these rows (journal/score.py), never a model's opinion."""

    __tablename__ = "prediction"
    __table_args__ = (Index("ix_prediction_owner_status", "owner_person_id", "status"),)

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"))
    statement: Mapped[str] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text, default="")  # the owner's own words that this came from
    reason: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str] = mapped_column(String(20), default="other")  # timeline | people | money | work | other
    confidence_pct: Mapped[int] = mapped_column(Integer)  # 50..99: how sure the owner was
    confidence_inferred: Mapped[bool] = mapped_column(Boolean, default=False)  # from hedging words, not a number
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(12), default="manual")  # chat | manual
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("event.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | resolved
    came_true: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    resolution_note: Mapped[str] = mapped_column(Text, default="")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
