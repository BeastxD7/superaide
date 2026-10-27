"""Tables of `intentions/` (ADR 0015 phase 7): moved out of platform/db/models.py unchanged."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class Intention(Scoped, Base):
    """Something the owner wants to remember to do: a reminder or a follow-up.

    Prospective memory: it comes back at the right moment (a time today; later also
    a cue, such as the person messaging). Delivery runs as a job keyed on the
    intention and its due time, so it is sent once even if scheduled twice.
    """

    __tablename__ = "intention"
    __table_args__ = (Index("ix_intention_owner_status_due", "owner_person_id", "status", "due_at"),)

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"))
    kind: Mapped[str] = mapped_column(String(20), default="reminder")  # reminder | follow_up
    text: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | done | cancelled
    source: Mapped[str] = mapped_column(String(20), default="chat")  # chat | dashboard
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("event.id"), nullable=True)
    # Where it is delivered besides the app: default (the owner's interruption choice) | app | telegram | slack
    channel: Mapped[str] = mapped_column(String(12), default="default", server_default="default")
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_via: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
