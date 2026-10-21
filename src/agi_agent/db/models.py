"""Database tables: identity, conversations, audience-gated memory, approvals,
audit, the spend ledger, and user accounts for the app.

Rules baked into the schema:
- Every row carries tenant_id and agent_id.
- Events, link events and audit rows are append-only (the code never updates them).
- A channel identity is unique per (provider, tenant_scope, stable_key) and is
  keyed on the platform's immutable ID, never on a display name.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from agi_agent.config import get_settings


def _tenant() -> uuid.UUID:
    return get_settings().tenant_id


def _agent() -> uuid.UUID:
    return get_settings().agent_id


class Base(DeclarativeBase):
    pass


class Scoped:
    """Columns shared by every table."""

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=_tenant, index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=_agent, index=True)
    # Set in Python so rows written in one transaction keep their order
    # (Postgres now() is the same for a whole transaction).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=func.now()
    )


# --- Identity -----------------------------------------------------------------


class Person(Scoped, Base):
    __tablename__ = "person"

    display_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="human")  # human | agent
    # Authority comes from here (set by the operator), never from what a message claims.
    relation: Mapped[str] = mapped_column(String(20), default="unknown")  # manager | colleague | external | unknown
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | merged | erased
    merged_into: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    channel_prefs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    notice_basis: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class ChannelIdentity(Scoped, Base):
    __tablename__ = "channel_identity"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", "tenant_scope", "stable_key", name="uq_identity_key"),)

    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id"), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(20))  # telegram | slack | email | ...
    tenant_scope: Mapped[str] = mapped_column(String(100), default="")  # e.g. Slack team ID
    stable_key: Mapped[str] = mapped_column(String(200))  # immutable platform user ID
    # Display only, never used for authorisation.
    aux_handles: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    trust_level: Mapped[int] = mapped_column(Integer, default=1)
    verification: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    lifecycle: Mapped[str] = mapped_column(String(20), default="active")  # active | superseded | revoked
    risk_flags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdentityLinkEvent(Scoped, Base):
    __tablename__ = "identity_link_event"

    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channel_identity.id"), index=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(20))  # link | unlink | supersede | merge | split
    method: Mapped[str] = mapped_column(String(50))
    actor: Mapped[str] = mapped_column(String(100))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class LinkChallenge(Scoped, Base):
    __tablename__ = "link_challenge"

    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channel_identity.id"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"))
    via_identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channel_identity.id"))
    code_hash: Mapped[str] = mapped_column(String(128))
    salt: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | verified | expired | locked


# --- Conversations ---------------------------------------------------------------


class Context(Scoped, Base):
    __tablename__ = "context"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", "tenant_scope", "external_id", name="uq_context_key"),)

    provider: Mapped[str] = mapped_column(String(20))
    tenant_scope: Mapped[str] = mapped_column(String(100), default="")
    external_id: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))  # dm | group | thread | meeting
    audience_class: Mapped[str] = mapped_column(String(30))  # private_1to1 | team | mixed_external
    title: Mapped[str] = mapped_column(String(200), default="")


class Event(Scoped, Base):
    """Every message in and out: the raw episodic log. Append-only."""

    __tablename__ = "event"

    context_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("context.id"), index=True)
    direction: Mapped[str] = mapped_column(String(10))  # in | out
    sender_identity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("channel_identity.id"), nullable=True)
    resolved_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    trust_at_time: Mapped[int] = mapped_column(Integer, default=0)
    audience_person_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    audience_has_unknown: Mapped[bool] = mapped_column(Boolean, default=False)
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    webhook_verified: Mapped[bool] = mapped_column(Boolean, default=False)


# --- Memory ------------------------------------------------------------------------


class MemoryItem(Scoped, Base):
    """A remembered fact, tagged with the audience it may be shown to."""

    __tablename__ = "memory_item"

    content: Mapped[str] = mapped_column(Text)
    subject_person_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    source_event_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    origin_context_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("context.id"), nullable=True)
    # persons: only the listed people · org: any known internal person · public: anyone
    allowed_kind: Mapped[str] = mapped_column(String(20), default="persons")
    allowed_person_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    min_trust: Mapped[int] = mapped_column(Integer, default=2)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- Governance ----------------------------------------------------------------


class Approval(Scoped, Base):
    __tablename__ = "approval"

    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    requested_by_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Scoped, Base):
    """Append-only record of decisions, model calls and refused actions."""

    __tablename__ = "audit_log"

    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(60), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class SpendEntry(Scoped, Base):
    """One Claude API call: reserved before the call, settled after it."""

    __tablename__ = "spend_ledger"

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


# --- User accounts and sessions ----------------------------------------------------


class TenantScoped:
    """Columns for tables that belong to the tenant rather than to one agent."""

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=_tenant, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=func.now()
    )


class UserAccount(TenantScoped, Base):
    """Someone who can sign in to the app. Passwords are stored only as Argon2id hashes.

    Each account is tied to one Person: the same human as seen by the agents, so
    the app, Telegram and Slack all resolve to one record.
    """

    __tablename__ = "user_account"
    __table_args__ = (UniqueConstraint("tenant_id", "email_normalized", name="uq_user_email"),)

    email: Mapped[str] = mapped_column(String(320))
    email_normalized: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120))
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), unique=True)
    role: Mapped[str] = mapped_column(String(20), default="user")  # user | admin
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | disabled
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Singapore")
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class UserSession(TenantScoped, Base):
    """One signed-in browser. The cookie holds a random token; only its SHA-256 is stored."""

    __tablename__ = "user_session"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")


class AuthEvent(TenantScoped, Base):
    """Append-only record of sign-ups, logins, failures and logouts (rate limits read it)."""

    __tablename__ = "auth_event"

    kind: Mapped[str] = mapped_column(String(30), index=True)  # signup | login_ok | login_fail | logout | ...
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
    email_normalized: Mapped[str] = mapped_column(String(320), default="", index=True)
    ip: Mapped[str] = mapped_column(String(64), default="", index=True)
    user_agent: Mapped[str] = mapped_column(String(300), default="")


class AgentHire(TenantScoped, Base):
    """Which account hired which agent, and the notice they agreed to. One active hire per agent."""

    __tablename__ = "agent_hire"
    __table_args__ = (
        Index(
            "uq_agent_hire_active", "tenant_id", "agent_slug", unique=True, postgresql_where=text("status = 'active'")
        ),
    )

    agent_slug: Mapped[str] = mapped_column(String(40))
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | ended
    notice: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Onboarding(Scoped, Base):
    """Where the person is in the agent's onboarding conversation."""

    __tablename__ = "onboarding"

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), unique=True)
    step: Mapped[str] = mapped_column(String(30), default="hello")
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChannelInvite(Scoped, Base):
    """A one-time link made in the signed-in app, opened in Telegram to connect it."""

    __tablename__ = "channel_invite"

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"))
    provider: Mapped[str] = mapped_column(String(20))
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("channel_identity.id"), nullable=True)


class AgentState(Base):
    """Small key-value store for switches such as the kill switch."""

    __tablename__ = "agent_state"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_tenant)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_agent)
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
