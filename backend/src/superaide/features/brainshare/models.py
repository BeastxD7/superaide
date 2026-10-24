"""An unlisted share link for the counts-only picture of the owner's memory (Glass brain, #55).

Platform-wide lookup table like `memory_socket_client` (db/rls.py EXEMPT): a visitor arrives with only a
token, so the row is read before any instance is in scope. It holds a hash of the token (the token is shown
once and cannot be recovered), when the link expires (at most 7 days, also enforced here by a check) and
whether the owner revoked it. It holds no memory and no name. Every owner-side query filters `agent_id`
explicitly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class BrainShare(Base):
    __tablename__ = "brain_share"
    __table_args__ = (
        CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '7 days'", name="ck_brain_share_max_7_days"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_instance.id"), index=True)
    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 of the token; the token is shown once
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    views: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
