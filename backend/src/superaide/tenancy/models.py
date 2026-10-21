"""Tables for many owners (ADR 0009): the customer, each hired agent instance, who
belongs to it, the channel endpoints that route messages to it, and platform state.

`agent_instance.id` is the `agent_id` every scoped row already carries, and
`tenant.id` its `tenant_id`, so existing data needs no rewrite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from superaide.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())


class Tenant(Base):
    """The customer: who pays and administers. Today one per owner account (a personal workspace)."""

    __tablename__ = "tenant"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(20), default="personal")  # personal | org
    name: Mapped[str] = mapped_column(String(200), default="")
    region: Mapped[str] = mapped_column(String(40), default="")  # where its data lives; "" = the default region
    default_locale: Mapped[str] = mapped_column(String(35), default="en")
    default_timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    billing_currency: Mapped[str] = mapped_column(String(3), default="USD")
    plan: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | suspended | deleted
    created_at: Mapped[datetime] = _created()


class AgentInstance(Base):
    """One hire of an agent product by one owner. Its id is the `agent_id` of all its data."""

    __tablename__ = "agent_instance"
    __table_args__ = (
        Index(
            "uq_agent_instance_owner_active",
            "owner_user_id",
            "agent_slug",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), index=True)
    agent_slug: Mapped[str] = mapped_column(String(40))  # ava | leo | kai
    display_name: Mapped[str] = mapped_column(String(120), default="")
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | paused | ended
    region: Mapped[str] = mapped_column(String(40), default="")
    locale: Mapped[str] = mapped_column(String(35), default="en")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    key_source: Mapped[str] = mapped_column(String(20), default="owner")  # owner | platform: who pays for the model
    # This instance's own spend limits over the .env defaults: any of lifetime_usd, daily_usd,
    # monthly_usd, daily_by_bucket ({"chat": 0.4, ...}). Empty: the defaults apply.
    spend_limits: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _created()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InstanceMember(Base):
    """An account's Person inside one instance (People are per instance, never shared)."""

    __tablename__ = "instance_member"
    __table_args__ = (UniqueConstraint("agent_id", "user_id", name="uq_instance_member"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_instance.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"))
    role: Mapped[str] = mapped_column(String(20), default="owner")  # owner | admin
    created_at: Mapped[datetime] = _created()


class ChannelEndpoint(Base):
    """Where inbound messages for one instance arrive: a bot, a Slack install, a business connection.
    A message is routed to an instance by its endpoint before anything else happens."""

    __tablename__ = "channel_endpoint"
    __table_args__ = (UniqueConstraint("provider", "external_key", name="uq_channel_endpoint_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_instance.id"), index=True)
    provider: Mapped[str] = mapped_column(String(20))  # telegram | slack | line
    # own_bot | managed_bot | business_conn | slack_install | platform_bot
    kind: Mapped[str] = mapped_column(String(30))
    # Telegram bot id, Slack "team_id[:enterprise_id]", or a business_connection_id
    external_key: Mapped[str] = mapped_column(String(200))
    webhook_secret_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | disabled
    created_at: Mapped[datetime] = _created()


class PlatformSpend(Base):
    """Platform-key spend per UTC day, for the platform-wide allowance (brain/platform_spend.py)."""

    __tablename__ = "platform_spend"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    committed_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal(0))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=_now, onupdate=_now
    )


class PlatformState(Base):
    """Platform-wide switches and counters (the platform freeze, the platform spend guard)."""

    __tablename__ = "platform_state"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=_now, onupdate=_now
    )
