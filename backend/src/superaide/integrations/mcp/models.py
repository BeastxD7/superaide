"""The owner's MCP servers (issue #666). One row per server, per hired Ava (agent_id, with Row-Level Security)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class McpServer(Scoped, Base):
    """A server the owner added. Its secrets (environment variables, HTTP headers) are not here: they are one sealed
    blob in the vault (`IntegrationSecret`, provider `mcp`, name = this row's id), and only hints are kept here."""

    __tablename__ = "mcp_server"
    __table_args__ = (UniqueConstraint("agent_id", "slug", name="uq_mcp_server_slug"),)

    slug: Mapped[str] = mapped_column(String(24))  # a-z 0-9 -, part of every tool name; never changes
    label: Mapped[str] = mapped_column(String(80))
    transport: Mapped[str] = mapped_column(String(10))  # stdio | http
    command: Mapped[str] = mapped_column(String(300), default="")
    args: Mapped[list[str]] = mapped_column(JSONB, default=list)
    url: Mapped[str] = mapped_column(String(500), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_s: Mapped[int] = mapped_column(Integer, default=30)
    calls_per_hour: Mapped[int] = mapped_column(Integer, default=60)
    # The last `tools/list` result as the server gave it: [{name, description, inputSchema, annotations}].
    tools: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # The owner's class per tool, bound to the tool's hash: {tool: {"class": "read|write|destructive", "hash": "..."}}.
    risk: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Names and masked hints of the secrets, never values: {"env": {KEY: hint}, "headers": {Name: hint}}.
    secret_hints: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    listed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(String(300), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
