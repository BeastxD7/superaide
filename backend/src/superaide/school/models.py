"""A lesson, owner-only. Per agent instance (`Scoped`), under Row-Level Security."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from superaide.db.models import Base, Scoped


class Lesson(Scoped, Base):
    __tablename__ = "lesson"

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    role: Mapped[str] = mapped_column(String(60), default="personal assistant")
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10), default="own")  # own | imported
    # own: active at once. imported: pending until the owner accepts it. Nothing reads an active lesson yet.
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | pending
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # to share
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
