"""Tables of `mcp/` (ADR 0015 phase 7): moved out of platform/db/models.py unchanged."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class McpPin(Scoped, Base):
    """The hash and text of one MCP tool as the owner approved it (issue #116)."""

    __tablename__ = "mcp_pin"
    __table_args__ = (UniqueConstraint("agent_id", "skill_id", "server", "tool", name="uq_mcp_pin"),)

    skill_id: Mapped[str] = mapped_column(String(120))
    server: Mapped[str] = mapped_column(String(300))
    tool: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
