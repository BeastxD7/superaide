"""Tables of `proactive/`: one row per message pushed to the owner's own Telegram or Slack."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class NotificationDelivery(Scoped, Base):
    """Did this message reach the owner on the channel they asked for? (#491)

    The in-app copy is always there; this row tracks the push to Telegram or Slack, so a failed one is retried
    (by a job, with back-off) and, when it never gets through, stays visible as "not delivered".
    The text is the notification the owner already has in the app.
    """

    __tablename__ = "notification_delivery"
    __table_args__ = (Index("ix_notification_delivery_state", "agent_id", "state"),)

    channel: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # queued (first try failed, retry booked) | sent | failed (retries used up or can never work)
    # | rerouted (given up on this channel; a row on another channel carries it) | acked (the owner saw it)
    state: Mapped[str] = mapped_column(String(12), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
