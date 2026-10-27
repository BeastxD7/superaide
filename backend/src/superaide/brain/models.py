"""Tables of `brain/` (ADR 0015 phase 7): moved out of platform/db/models.py unchanged."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class SpendEntry(Scoped, Base):
    """One model call: reserved before the call, settled after it."""

    __tablename__ = "spend_ledger"

    provider: Mapped[str] = mapped_column(String(40), default="anthropic", server_default="anthropic")
    # Who paid for the model: the owner's own key, or the platform's (ADR 0009 T6).
    key_source: Mapped[str] = mapped_column(String(20), default="owner", server_default="owner")
    purpose: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="reserved")  # reserved | settled | failed
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OwnerModelPrice(Scoped, Base):
    """A price the owner set for a model Superaide has no built-in price for (US$ per million tokens)."""

    __tablename__ = "owner_model_price"
    __table_args__ = (UniqueConstraint("agent_id", "provider", "model", name="uq_owner_model_price"),)

    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    input_usd_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    output_usd_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    cache_read_mult: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=Decimal(1))
    set_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class LlmProvider(Scoped, Base):
    """A model provider the owner connected for this Ava (ADR 0007). Its key is in the vault
    (integration_secret, provider "llm:<key>"), never here."""

    __tablename__ = "llm_provider"
    __table_args__ = (UniqueConstraint("agent_id", "key", name="uq_llm_provider_key"),)

    # This endpoint's own name: the preset for the first one, e.g. "custom-2" for another. Roles,
    # prices, the ledger and the vault slot (llm:<key>) all use it.
    key: Mapped[str] = mapped_column(String(24))
    preset: Mapped[str] = mapped_column(
        String(20)
    )  # openai | azure | openrouter | xai | gemini | ollama | vllm | custom
    base_url: Mapped[str] = mapped_column(String(300), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # e.g. models seen at the last check
    set_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class ModelRole(Scoped, Base):
    """Which provider and model this Ava uses for a kind of work: chat, triage or deep."""

    __tablename__ = "model_role"
    __table_args__ = (UniqueConstraint("agent_id", "role", name="uq_model_role"),)

    role: Mapped[str] = mapped_column(String(10))  # chat | triage | deep
    provider: Mapped[str] = mapped_column(String(20))  # anthropic, or an llm_provider preset
    model: Mapped[str] = mapped_column(String(120))
    set_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
