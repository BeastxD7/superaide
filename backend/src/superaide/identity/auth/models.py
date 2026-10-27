"""One-time email links: verifying an address and resetting a password."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from superaide.platform.db.models import EMAIL_MATCH_SQL, Base, TenantScoped, _agent


class EmailToken(Base):
    """A link sent by email. Only the SHA-256 of the token is stored; each one works once."""

    __tablename__ = "email_token"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))  # verify_email | reset_password
    email_normalized: Mapped[str] = mapped_column(String(320))  # the address it was sent to
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=func.now()
    )


class UserAccount(TenantScoped, Base):
    """Someone who can sign in to the app. Passwords are stored only as Argon2id hashes.

    Each account is tied to one Person: the same human as seen by the agents, so
    the app, Telegram and Slack all resolve to one record.
    """

    __tablename__ = "user_account"
    # Accounts are platform-wide: one sign-in for every agent the person hires (ADR 0009).
    __table_args__ = (
        UniqueConstraint("email_normalized", name="uq_user_email"),
        UniqueConstraint("google_sub", name="uq_user_google_sub"),
        # The two email columns can't drift apart (a hand edit of one once broke a sign-in rule): for
        # plain ASCII addresses, which is nearly all of them, the normalized one must be the address
        # lower-cased. Non-ASCII addresses are normalized in code (auth/service.normalize_email).
        CheckConstraint(EMAIL_MATCH_SQL, name="ck_user_email_normalized"),
    )

    email: Mapped[str] = mapped_column(String(320))
    email_normalized: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(String(255))  # "" when the account signs in with Google only
    # Google's stable account id ("sub"), once the account has signed in with Google. The account is
    # found by this, never by email alone: a Google account's address can change or be reused.
    google_sub: Mapped[str | None] = mapped_column(String(255), nullable=True)

    @validates("email")
    def _keep_normalized(self, _key: str, value: str) -> str:
        """Setting the address always sets its normalized form too."""
        from superaide.identity.auth.service import AuthError, normalize_email

        try:
            self.email_normalized = normalize_email(value)
        except AuthError:
            self.email_normalized = value.strip().lower()
        return value

    display_name: Mapped[str] = mapped_column(String(120))
    # The account's own Person inside the Ava it hired; none until it hires (a Person belongs to one
    # agent instance, and an account that has not hired belongs to none).
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id"), unique=True, nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="user")  # user | admin
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | disabled
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")  # set from the browser at sign-up
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


class AccountAudit(Base):
    """Append-only record of events about an account that belongs to no Ava (yet): sign-up, e-mail checks,
    password changes before the first hire. Platform-level, so it has no agent_id and no isolation policy;
    once the account hires, the same kind of line goes to its own Ava's audit_log (superaide.platform.audit)."""

    __tablename__ = "account_audit"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(60), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=func.now()
    )


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


class IntegrationSecret(TenantScoped, Base):
    """One credential for a connected service, encrypted (see superaide.identity.vault).
    The plaintext never leaves the server; the app only ever shows `hint`.
    Each hired Ava has its own (ADR 0009 T3): the instance is part of the encryption's
    associated data, so a ciphertext copied to another instance won't decrypt."""

    __tablename__ = "integration_secret"
    __table_args__ = (UniqueConstraint("agent_id", "provider", "name", name="uq_integration_secret_slot"),)

    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=_agent, index=True)
    # "instance": sealed with the instance in its associated data; "legacy": sealed before that (resealed on use).
    aad: Mapped[str] = mapped_column(String(10), default="instance", server_default="instance")

    provider: Mapped[str] = mapped_column(String(30))  # anthropic | telegram | slack
    name: Mapped[str] = mapped_column(String(40))  # api_key | bot_token | app_token | team_id
    ciphertext: Mapped[str] = mapped_column(Text)
    hint: Mapped[str] = mapped_column(String(40), default="")  # masked, e.g. sk-ant-…4f2a
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # e.g. the bot's @username
    set_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
