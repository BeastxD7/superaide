"""`reply_draft`: a reply drafted for the owner to approve (#485). Per agent instance (`Scoped`), under RLS.

One row per (sender, latest message of theirs). The text is a suggestion: nothing is sent until the owner taps
send, and then it goes out through the same approval and executor as any relayed message (`reception/relay.py`).
The model only proposes the words; code picks who gets it, wraps it in the fixed introduction and re-checks
everything at send time. Rows are never deleted: a draft ends `sent`, `dismissed` or `stale` (they wrote again)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped

STATES = ("suggested", "sent", "dismissed", "stale")


def _now() -> datetime:
    return datetime.now(UTC)


class ReplyDraft(Scoped, Base):
    __tablename__ = "reply_draft"
    __table_args__ = (
        Index("uq_reply_draft_message", "identity_id", "source_message_id", unique=True),
        Index("ix_reply_draft_owner_state", "agent_id", "owner_person_id", "state"),
    )

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"))
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channel_identity.id"))
    context_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("context.id"))
    source_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reception_message.id")
    )  # the latest one it answers
    provider: Mapped[str] = mapped_column(String(20))
    recipient: Mapped[str] = mapped_column(String(20), default="")  # their first name, as they gave it: display only
    text: Mapped[str] = mapped_column(Text, default="")  # without the fixed introduction (added at send time)
    adds: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)  # times, numbers and days the sender did not say
    origin: Mapped[str] = mapped_column(
        String(6), default="model"
    )  # model | code (no model, or its words were refused)
    state: Mapped[str] = mapped_column(String(10), default="suggested")
    edited: Mapped[bool] = mapped_column(Boolean, default=False)  # the owner changed the words before sending
    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approval.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
