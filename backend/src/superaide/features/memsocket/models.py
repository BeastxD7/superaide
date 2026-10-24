"""A consumer of the Memory Socket (#138): one row per other assistant the owner lets read Ava's memory.

Platform-wide lookup table like `ask_handle` (db/rls.py EXEMPT): a call arrives with only a token, so the row
is read before any instance is in scope. It holds a hash of the token, the consumer's name and the scope the
owner chose, and no memory. Every query on it filters `agent_id` explicitly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class MemorySocketClient(Base):
    __tablename__ = "memory_socket_client"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_instance.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    scope: Mapped[str] = mapped_column(String(10))  # public | owner
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 of the token; the token is shown once
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
