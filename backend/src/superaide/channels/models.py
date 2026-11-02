"""Tables of `channels/`: which rooms the owner let Ava read in full (#509, opt-in, off by default)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class AmbientChannel(Scoped, Base):
    """One channel and the owner's choice about it. A channel with no row, or `enabled = false`, is read only when
    someone mentions Ava (the old behaviour). Messages stored from an enabled channel carry the room's audience
    (everyone who can read the room), are never sent to a model when they arrive, and age out after `retention_days`."""

    __tablename__ = "ambient_channel"
    __table_args__ = (
        UniqueConstraint("agent_id", "provider", "tenant_scope", "external_id", name="uq_ambient_channel_key"),
    )

    provider: Mapped[str] = mapped_column(String(20))
    tenant_scope: Mapped[str] = mapped_column(String(100), default="")
    external_id: Mapped[str] = mapped_column(String(200))  # the platform's channel id
    title: Mapped[str] = mapped_column(String(200), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    retention_days: Mapped[int] = mapped_column(Integer, default=30)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_heard_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # volume cap hit
