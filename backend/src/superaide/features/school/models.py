"""A lesson, owner-only. Per agent instance (`Scoped`), under Row-Level Security."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class Lesson(Scoped, Base):
    __tablename__ = "lesson"
    __table_args__ = (
        # A lesson reaches a prompt only after its eval passed and only while active: the database refuses
        # anything else, whatever the code above it does (#57).
        CheckConstraint(
            "NOT in_prompt OR (eval_status = 'passed' AND status = 'active')", name="ck_lesson_prompt_eval"
        ),
        CheckConstraint("eval_status in ('none', 'passed', 'failed')", name="ck_lesson_eval_status"),
    )

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    role: Mapped[str] = mapped_column(String(60), default="personal assistant")
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10), default="own")  # own | imported
    # own: active at once. imported: pending until the owner accepts it. An active lesson is read by nothing unless
    # the owner wrote playbook text for it, its eval passed and the owner switched it on (in_prompt).
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | pending
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # to share
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # The owner's own words for the prompt (never the imported text itself), the eval that guards it, and the switch.
    playbook_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    eval_spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)  # a scenario-shaped turn
    eval_status: Mapped[str] = mapped_column(String(10), default="none", server_default="none")
    eval_hash: Mapped[str] = mapped_column(String(64), default="", server_default="")  # playbook text + spec it ran on
    eval_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    eval_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    in_prompt: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
