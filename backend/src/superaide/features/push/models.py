"""Push tables (ADR 0011 section 3.1). Both are per agent instance (`Scoped`: tenant_id, agent_id),
so Row-Level Security (db/rls.py) keeps each owner's devices apart.

- `push_subscription`: one browser (later, one phone) that may receive the owner's pushes. The
  endpoint and its keys let anyone push to the device, so they are stored only encrypted, with
  the instance and row id as associated data ("{agent_id}/push/{id}"): a row copied to another
  instance fails to decrypt. `endpoint_hash` (SHA-256) is what uniqueness and lookups use.
- `push_send`: one attempt to reach one device, for health and for retries that skip devices
  already reached. Holds status codes and ids, never payload text.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from superaide.db.models import Base, Scoped


def _now() -> datetime:
    return datetime.now(UTC)


class PushSubscription(Scoped, Base):
    __tablename__ = "push_subscription"
    __table_args__ = (
        UniqueConstraint("agent_id", "endpoint_hash", name="uq_push_subscription_endpoint"),
        Index("ix_push_subscription_agent_person", "agent_id", "person_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"))
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"))  # the instance owner
    # The signed-in browser session that made it (web). Signing that session out removes it (M5).
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_session.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(10), default="webpush")  # webpush | apns | fcm (later)
    endpoint_hash: Mapped[str] = mapped_column(String(64))
    secret_enc: Mapped[str] = mapped_column(Text)  # AES-256-GCM of {endpoint, p256dh, auth}
    push_host: Mapped[str] = mapped_column(String(200), default="")  # e.g. web.push.apple.com; not secret
    device_label: Mapped[str] = mapped_column(String(80), default="")
    platform: Mapped[str] = mapped_column(String(10), default="other")  # ios | ipados | android | desktop | other
    preview_level: Mapped[str] = mapped_column(String(10), default="none")  # none | sender | reason
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    gone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PushSend(Scoped, Base):
    __tablename__ = "push_send"
    __table_args__ = (
        UniqueConstraint("delivery_id", "subscription_id", name="uq_push_send_delivery_device"),
        Index("ix_push_send_agent_at", "agent_id", "created_at"),
    )

    # The ladder rung this push belongs to (ADR 0010); null for tests, digests and reminders.
    delivery_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("escalation_delivery.id"), nullable=True)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("push_subscription.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(12))  # escalation | digest | reminder | test
    status: Mapped[str] = mapped_column(String(8))  # sent | failed | gone
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str] = mapped_column(String(200), default="")
