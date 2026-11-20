"""Standing rules (#486): what the owner has declared Ava may say or must never discuss, as structured rows.

Per agent instance (`Scoped`), under Row-Level Security. Rules are written by the owner only (the settings API);
the model never creates, edits or reads the table.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class StandingRule(Scoped, Base):
    """kind `allow_statement`: `text` is a sentence the owner declared Ava may say when asked about their
    whereabouts or availability. kind `forbid`: `topic` is one key of `standing.FORBID_TOPICS`, never free text.
    scope `everyone` applies to every non-owner; scope `contact` only to `contact_person_id`."""

    __tablename__ = "standing_rule"
    __table_args__ = (Index("ix_standing_rule_agent_live", "agent_id", "revoked_at", "expires_at"),)

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    scope: Mapped[str] = mapped_column(String(10), default="everyone")  # everyone | contact
    contact_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(16))  # allow_statement | forbid
    text: Mapped[str] = mapped_column(String(160), default="")
    topic: Mapped[str] = mapped_column(String(24), default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
