"""`presence_state`: what the owner said about being reachable, with an end time. Per agent instance (`Scoped`), under
Row-Level Security. Code writes it from the owner's own words (parse.py); the model never does. A state past its end
does nothing (`service.active`)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class PresenceState(Scoped, Base):
    __tablename__ = "presence_state"
    __table_args__ = (Index("ix_presence_state_person_status", "person_id", "status", "valid_until"),)

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"))
    kind: Mapped[str] = mapped_column(String(12))  # busy | driving | focus | travel | away
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Contact relationships that still get through ("family"); "vip" lets VIP contacts through.
    allow_relations: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    # The only words a visitor hears, built by code from a fixed template (never the kind, never a reason).
    visitor_line: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(10), default="owner")  # owner | calendar (later)
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | cleared | ended
