"""`import_lease`: one time-limited permission to hold cards imported from a third-party export."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from superaide.db.models import Base, Scoped


class ImportLease(Scoped, Base):
    __tablename__ = "import_lease"

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    source_kind: Mapped[str] = mapped_column(String(20))  # takeout | chat_archive | health | notes | workspace
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(30)), default=list)  # what the owner allowed
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | ended | expired
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    card_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
