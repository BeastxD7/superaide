"""Tables for the replay features (issue #110). Per agent instance (`Scoped`) with RLS."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class UpdatePreview(Scoped, Base):
    """What a skill, prompt or model change would have done differently on the owner's recent tasks (#110).

    The summary holds tool names, effect classes and argument-hash prefixes only: never message text, results or
    anyone else's content. The owner approves or rejects it; nothing here applies the change by itself.
    """

    __tablename__ = "update_preview"
    __table_args__ = (
        CheckConstraint("kind in ('skill', 'prompt', 'model')", name="ck_update_preview_kind"),
        CheckConstraint("status in ('pending', 'approved', 'rejected')", name="ck_update_preview_status"),
    )

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    label: Mapped[str] = mapped_column(String(120))  # the skill id, the model name, or "prompt change"
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # skill_id | model | prompt hash and length
    tasks_asked: Mapped[int] = mapped_column(Integer, default=0)
    tasks_done: Mapped[int] = mapped_column(Integer, default=0)
    changed: Mapped[int] = mapped_column(Integer, default=0)  # how many of the tasks would go differently
    summary: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # per task: same, only_before, only_after
    stopped: Mapped[str] = mapped_column(String(120), default="")  # why it ended early, else empty
    status: Mapped[str] = mapped_column(String(10), default="pending")
    decided_by_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ToolTrace(Scoped, Base):
    """One tool call, as hashes only (issue #110): enough to compare two runs, never the content."""

    __tablename__ = "tool_trace"

    turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    tool: Mapped[str] = mapped_column(String(64))
    effect: Mapped[str] = mapped_column(String(20))
    args_hash: Mapped[str] = mapped_column(String(64))
    result_hash: Mapped[str] = mapped_column(String(64))
    is_error: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str] = mapped_column(String(80), default="")
    skills: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # skill id -> version in this turn
    replay: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
