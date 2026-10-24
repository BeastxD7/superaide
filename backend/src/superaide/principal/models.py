"""`card_link`: a short card link (#438) to the instance and card it names.

The public link `/c/<code>` is read before any instance is in scope (a recipient has no account), so this
table is exempt from Row-Level Security like `ask_handle` (platform/db/rls.py EXEMPT). It holds no personal
data: a random code, which instance and card it belongs to, the card generation it was issued under and its
expiry. The card's name and lists are read from the owner's current settings when the link is opened.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class CardLink(Base):
    __tablename__ = "card_link"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(16), unique=True)  # 8 URL-safe characters
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    card_id: Mapped[str] = mapped_column(String(16))  # the id the owner's sent list and revoke use
    generation: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())
