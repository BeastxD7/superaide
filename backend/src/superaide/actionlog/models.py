"""Tables of `actionlog/` (ADR 0015 phase 7): moved out of platform/db/models.py unchanged."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class ActionLog(Scoped, Base):
    """Append-only, hash-chained (actionlog/chain.py, issue #113). Hashes and names only, never content."""

    __tablename__ = "action_log"
    __table_args__ = (UniqueConstraint("agent_id", "seq", name="uq_action_log_seq"),)

    seq: Mapped[int] = mapped_column(Integer)
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(12))  # call | executed
    tool: Mapped[str] = mapped_column(String(64))
    effect: Mapped[str] = mapped_column(String(20))
    args_hash: Mapped[str] = mapped_column(String(64), default="")
    result_hash: Mapped[str] = mapped_column(String(64), default="")
    approval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
