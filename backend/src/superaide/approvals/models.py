"""Tables of `approvals/` (ADR 0015 phase 7): moved out of platform/db/models.py unchanged."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, text
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
