"""Tables of `features/mirror/` (ADR 0015 phase 7): moved out of platform/db/models.py unchanged."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import Boolean, Date, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class MirrorSetting(Scoped, Base):
    """Whether this owner gets "The Mirror" (a weekly note of patterns with receipts), and what they muted."""

    __tablename__ = "mirror_setting"

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # "Don't mention this again": exact patterns the owner muted (a pattern key names the thing, not the week).
    muted_patterns: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    # "Not useful" taps per kind of pattern; a kind with two stops being shown.
    not_useful: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class MirrorNote(Scoped, Base):
    """One week's note: up to three findings, each with the rows it was counted from."""

    __tablename__ = "mirror_note"
    __table_args__ = (UniqueConstraint("owner_person_id", "week_start", name="uq_mirror_note_week"),)

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    week_start: Mapped[date] = mapped_column(Date)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
