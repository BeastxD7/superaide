"""Tables of `integrations/calendar/` (ADR 0015 phase 7): moved out of platform/db/models.py unchanged."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class CalendarWatch(Scoped, Base):
    """One Google Calendar push channel (events.watch) on an owner's primary calendar. Google calls
    the webhook with the channel's token when events change; see proactive/calendar_watch.py."""

    __tablename__ = "calendar_watch"
    __table_args__ = (UniqueConstraint("channel_id", name="uq_calendar_watch_channel"),)

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    channel_id: Mapped[str] = mapped_column(String(64))  # starts with the instance id; see calendar_watch.py
    resource_id: Mapped[str] = mapped_column(String(200), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | stopped | expired
