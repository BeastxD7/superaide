"""One rehearsal session, owner-only. Per agent instance (`Scoped`), under Row-Level Security."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


def _now() -> datetime:
    return datetime.now(UTC)


class RehearsalSession(Scoped, Base):
    __tablename__ = "rehearsal_session"

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    role: Mapped[str] = mapped_column(String(120))  # a generic role: "a sceptical investor"
    notes: Mapped[str] = mapped_column(Text, default="")  # what the owner told Ava about the situation
    turns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)  # [{"who": "owner"|"other", "text": ...}]
    debrief: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    kept: Mapped[bool] = mapped_column(Boolean, default=False)  # unkept sessions are deleted after a day
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
