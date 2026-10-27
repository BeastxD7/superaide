"""Tables of `identity/` (ADR 0015 phase 7): moved out of platform/db/models.py unchanged."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class Person(Scoped, Base):
    __tablename__ = "person"
    __table_args__ = (Index("ix_person_agent_created", "agent_id", "created_at", "id"),)  # operator paging (ADR 0014)

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
    __table_args__ = (
        UniqueConstraint("agent_id", "provider", "tenant_scope", "stable_key", name="uq_identity_key"),
        # Accounts nobody has claimed yet, paged by the operator console (ADR 0014); the partial index stays small.
        Index(
            "ix_identity_unlinked",
            "agent_id",
            "created_at",
            "id",
            postgresql_where=text("person_id IS NULL"),
        ),
    )

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
    # The endpoint (Telegram bot id, Slack app) this link was confirmed on. A message from the same
    # account through any other endpoint is not trusted until the owner confirms it in the app.
    # Same meaning as channel_endpoint.external_key (ADR 0009); "" = not bound yet.
    endpoint_key: Mapped[str] = mapped_column(String(200), default="", server_default="")


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


class IdentityClaim(Scoped, Base):
    """An unknown account whose name is a known person's name, and the owner's answer (issue #93)."""

    __tablename__ = "identity_claim"
    __table_args__ = (UniqueConstraint("agent_id", "identity_id", name="uq_identity_claim_identity"),)

    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channel_identity.id", ondelete="CASCADE"), index=True)
    matched_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    claimed_name: Mapped[str] = mapped_column(String(80), default="")
    # What the platform told us and the first message (display name, channel, user id, handle, phone if
    # supplied, time, excerpt). Never model output.
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | linked | new_person | stranger | blocked
    asks: Mapped[int] = mapped_column(Integer, default=1)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)


class ChannelInvite(Scoped, Base):
    """A one-time link made in the signed-in app, opened in Telegram to connect it."""

    __tablename__ = "channel_invite"

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"))
    provider: Mapped[str] = mapped_column(String(20))
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("channel_identity.id"), nullable=True)
    # The bot it was made for: opened through any other bot, it is refused. "" = any (older invites).
    endpoint_key: Mapped[str] = mapped_column(String(200), default="", server_default="")
    # A confirm-this-bot invite works only for the account that was waiting to be confirmed.
    for_identity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("channel_identity.id"), nullable=True)
