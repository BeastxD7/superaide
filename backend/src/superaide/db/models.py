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
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, validates


def _tenant() -> uuid.UUID:
    """The customer of the agent instance in scope (ADR 0009); the dev instance when none is."""
    from superaide.tenancy.context import scope_or_default

    return scope_or_default().tenant_id


def _agent() -> uuid.UUID:
    """The agent instance in scope: new rows belong to it (and Row-Level Security checks that they do)."""
    from superaide.tenancy.context import scope_or_default

    return scope_or_default().agent_id


class Base(DeclarativeBase):
    pass


@event.listens_for(Base, "init", propagate=True)
def _belongs_to_the_instance_in_scope(target: Any, args: Any, kwargs: dict[str, Any]) -> None:
    """A new row takes the agent instance in scope when it is created, not when it is flushed
    (which can be after the code has left that instance's scope). Values passed explicitly win."""
    mapper_columns = type(target).__mapper__.columns
    if "agent_id" in mapper_columns and "agent_id" not in kwargs:
        kwargs["agent_id"] = _agent()
    if "tenant_id" in mapper_columns and "tenant_id" not in kwargs and "agent_id" in mapper_columns:
        kwargs["tenant_id"] = _tenant()
    elif "tenant_id" in mapper_columns and "tenant_id" not in kwargs and type(target).__tablename__ != "tenant":
        kwargs["tenant_id"] = _tenant()


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
    __table_args__ = (UniqueConstraint("agent_id", "provider", "tenant_scope", "stable_key", name="uq_identity_key"),)

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


# --- Conversations ---------------------------------------------------------------


class Context(Scoped, Base):
    __tablename__ = "context"
    __table_args__ = (UniqueConstraint("agent_id", "provider", "tenant_scope", "external_id", name="uq_context_key"),)

    provider: Mapped[str] = mapped_column(String(20))
    tenant_scope: Mapped[str] = mapped_column(String(100), default="")
    external_id: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))  # dm | group | thread | meeting
    audience_class: Mapped[str] = mapped_column(String(30))  # private_1to1 | team | mixed_external
    title: Mapped[str] = mapped_column(String(200), default="")


class Event(Scoped, Base):
    """Every message in and out: the raw episodic log. Append-only."""

    __tablename__ = "event"
    __table_args__ = (
        Index(
            "uq_event_inbound_external_id",
            "context_id",
            "external_id",
            unique=True,
            postgresql_where=text("direction = 'in' AND external_id IS NOT NULL"),
        ),
        Index("ix_event_context_thread", "context_id", "thread_id"),
        Index("ix_event_fts", text("to_tsvector('english', content)"), postgresql_using="gin"),  # recall
    )

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
    # The platform's own ids: a redelivered message (Slack retry, Telegram re-poll)
    # hits the unique index below and is dropped; threads keep their own history.
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(120), nullable=True)


# --- Memory ------------------------------------------------------------------------


class MemoryItem(Scoped, Base):
    """A remembered fact, tagged with the audience it may be shown to."""

    __tablename__ = "memory_item"
    __table_args__ = (Index("ix_memory_item_fts", text("to_tsvector('english', content)"), postgresql_using="gin"),)

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
    # What kind of fact this is, where it came from, and how sure we are.
    # category: about | work | person | preference | urgency | availability | sharing | other
    category: Mapped[str] = mapped_column(String(30), default="other", server_default="other", index=True)
    # source: chat | intro_chat | intro_call | document | link | telegram | slack | owner_edit
    source: Mapped[str] = mapped_column(String(30), default="chat", server_default="chat")
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id"), nullable=True, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, server_default="1")
    # The import lease this card came in under (imports/lease.py, #133); ending the lease deletes the row.
    lease_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("import_lease.id"), nullable=True, index=True)
    # fact | hearsay (#137): hearsay came from outside the owner and has fewer than two independent roots.
    belief_state: Mapped[str] = mapped_column(String(10), default="fact", server_default="fact")

    # How much the fact matters (0..1), set in code at extraction. Ranks it in recall; and facts that
    # matter and last are re-checked with the owner now and then (knowledge/recheck.py).
    importance: Mapped[float] = mapped_column(Float, default=0.5, server_default="0.5")
    # Availability, plans, events: true means the fact fades as it ages. Everything else never decays.
    time_bound: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Spaced "is this still true?": when the owner last confirmed it, how many times, and when we last asked.
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verify_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    recheck_asked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Who wins when facts disagree (knowledge/trust.py): 1 = owner on a verified channel … 5 = other web.
    trust_rank: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    author_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("memory_item.id"), nullable=True)
    # none | pending (a weaker claim against a stronger fact) | review (a link fact outside about/work) | resolved
    conflict_status: Mapped[str] = mapped_column(String(10), default="none", server_default="none")
    conflicts_with_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("memory_item.id"), nullable=True)


class MemoryOrigin(Scoped, Base):
    """One root origin of a belief (#137). Repeats of the same root are one row, so they count once."""

    __tablename__ = "memory_origin"
    __table_args__ = (UniqueConstraint("memory_id", "origin_root", name="uq_memory_origin_root"),)

    memory_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("memory_item.id", ondelete="CASCADE"), index=True)
    origin_root: Mapped[str] = mapped_column(String(64))


class Episode(Scoped, Base):
    """A stretch of one conversation about one thing (#50). A pointer into the event log, which stays the
    source of truth: the summary helps find it, it never replaces it. Written only by the sleep job."""

    __tablename__ = "episode"

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    context_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("context.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="")
    participant_person_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    # Who may ever be shown this episode: the people in every one of its messages' audiences.
    audience_person_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    audience_has_unknown: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_by: Mapped[str] = mapped_column(String(20), default="extract")  # extract (code) | model
    hints: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)  # words to find it by
    importance: Mapped[float] = mapped_column(Float, default=0.5)


class ConsolidationRun(Scoped, Base):
    """One night's consolidation for one owner (#50): what it retired, saved first so it can be put back."""

    __tablename__ = "consolidation_run"

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | done | skipped
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)  # money ran out: only the cheap steps ran
    episodes_made: Mapped[int] = mapped_column(Integer, default=0)
    entries_before: Mapped[int] = mapped_column(Integer, default=0)
    retired_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    # The pre-image, written before anything is retired: [{id, content, valid_to}] for every retired entry.
    snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    notes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Document(Scoped, Base):
    """Something the owner shared for the agent to read: a file or a link.

    The extracted text is kept so facts can be traced back to it; the original
    file bytes are not stored. Removing a document also retires its facts.
    """

    __tablename__ = "document"
    __table_args__ = (UniqueConstraint("owner_person_id", "sha256", name="uq_document_owner_sha"),)

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # pdf | docx | text | web | github
    title: Mapped[str] = mapped_column(String(300), default="")
    source: Mapped[str] = mapped_column(Text, default="")  # file name or URL
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    # stored: text kept, facts not read yet (no model) · read: facts extracted · failed
    status: Mapped[str] = mapped_column(String(20), default="stored")
    warnings: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- The owner's people and preferences ----------------------------------------------


class Contact(Scoped, Base):
    """Someone who matters to the owner. They are a Person too, so the agent can
    recognise them once their Telegram, Slack or email is linked."""

    __tablename__ = "contact"
    __table_args__ = (UniqueConstraint("owner_person_id", "person_id", name="uq_contact_owner_person"),)

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"))
    relationship: Mapped[str] = mapped_column(String(30), default="other")  # family | partner | boss | team | ...
    vip: Mapped[bool] = mapped_column(Boolean, default=False)  # can always get through, even in quiet hours
    notes: Mapped[str] = mapped_column(Text, default="")
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OwnerPreference(Scoped, Base):
    """How the owner wants the assistant to triage, interrupt and share. One row per owner."""

    __tablename__ = "owner_preference"

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), unique=True)
    urgent_topics: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    digest: Mapped[str] = mapped_column(String(20), default="evening")  # morning | evening | both | none
    interrupt_channel: Mapped[str] = mapped_column(String(20), default="app")  # app | telegram | slack
    quiet_start: Mapped[str] = mapped_column(String(5), default="22:00")  # HH:MM, owner's timezone
    quiet_end: Mapped[str] = mapped_column(String(5), default="07:00")
    vip_breaks_quiet: Mapped[bool] = mapped_column(Boolean, default=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")  # set from the browser at sign-up
    shareable: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)  # what may be told to others
    never_share: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


# --- Governance ----------------------------------------------------------------


class Approval(Scoped, Base):
    __tablename__ = "approval"

    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    requested_by_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Issue #111: the hash of kind + payload when the request was opened (policy/binding.py), and when the
    # request stops being answerable (empty = never).
    bound_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Scoped, Base):
    """Append-only record of decisions, model calls and refused actions."""

    __tablename__ = "audit_log"

    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(60), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


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


# --- User accounts and sessions ----------------------------------------------------


class TenantScoped:
    """Columns for tables that belong to the tenant rather than to one agent."""

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=_tenant, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=func.now()
    )


EMAIL_MATCH_SQL = "octet_length(email) <> char_length(email) OR email_normalized = lower(btrim(email))"


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
        from superaide.auth.service import AuthError, normalize_email

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
    once the account hires, the same kind of line goes to its own Ava's audit_log (superaide.audit)."""

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


# --- Connections (encrypted third-party credentials) --------------------------------


class IntegrationSecret(TenantScoped, Base):
    """One credential for a connected service, encrypted (see superaide.vault).
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
    # The bot it was made for: opened through any other bot, it is refused. "" = any (older invites).
    endpoint_key: Mapped[str] = mapped_column(String(200), default="", server_default="")
    # A confirm-this-bot invite works only for the account that was waiting to be confirmed.
    for_identity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("channel_identity.id"), nullable=True)


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


class Job(Scoped, Base):
    """Background work: reminders, follow-ups, digests, the nightly memory job.

    Workers claim due rows with FOR UPDATE SKIP LOCKED, so two workers never run the
    same job, and `dedupe_key` makes enqueueing the same thing twice a no-op.
    """

    __tablename__ = "job"
    __table_args__ = (
        Index("ix_job_due", "status", "run_at"),
        UniqueConstraint("agent_id", "dedupe_key", name="uq_job_dedupe"),
    )

    kind: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # queued | running | done | failed | cancelled
    status: Mapped[str] = mapped_column(String(12), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)  # unique per instance


class Intention(Scoped, Base):
    """Something the owner wants to remember to do: a reminder or a follow-up.

    Prospective memory: it comes back at the right moment (a time today; later also
    a cue, such as the person messaging). Delivery runs as a job keyed on the
    intention and its due time, so it is sent once even if scheduled twice.
    """

    __tablename__ = "intention"
    __table_args__ = (Index("ix_intention_owner_status_due", "owner_person_id", "status", "due_at"),)

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"))
    kind: Mapped[str] = mapped_column(String(20), default="reminder")  # reminder | follow_up
    text: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | done | cancelled
    source: Mapped[str] = mapped_column(String(20), default="chat")  # chat | dashboard
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("event.id"), nullable=True)
    # Where it is delivered besides the app: default (the owner's interruption choice) | app | telegram | slack
    channel: Mapped[str] = mapped_column(String(12), default="default", server_default="default")
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_via: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InstalledSkill(Scoped, Base):
    """A skill the owner installed, with the permissions they agreed to. A newer version that
    asks for more must be agreed to again; until then the old grant is what's enforced."""

    __tablename__ = "installed_skill"
    __table_args__ = (UniqueConstraint("agent_id", "skill_id", name="uq_installed_skill"),)

    skill_id: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    granted: Mapped[list[str]] = mapped_column(ARRAY(String(30)), default=list)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CalendarWatch(Scoped, Base):
    """One Google Calendar push channel (events.watch) on an owner's primary calendar. Google calls
    the webhook with the channel's token when events change; see proactive/calendar_watch.py."""

    __tablename__ = "calendar_watch"
    __table_args__ = (UniqueConstraint("channel_id", name="uq_calendar_watch_channel"),)

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    channel_id: Mapped[str] = mapped_column(String(64))  # starts with the instance id; see calendar_watch.py
    resource_id: Mapped[str] = mapped_column(String(200), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | stopped | expired


# Tables defined in their own modules, imported here so every user of Base (Alembic,
# the tests' create_all) sees them.
from superaide.asklink import models as _asklink_models  # noqa: E402, F401
from superaide.auth import models as _auth_models  # noqa: E402, F401
from superaide.imports import models as _imports_models  # noqa: E402, F401
from superaide.jobs import claim as _job_claim  # noqa: E402, F401  (the claim function, made after the job table)
from superaide.push import models as _push_models  # noqa: E402, F401
from superaide.reception import models as _reception_models  # noqa: E402, F401
from superaide.tenancy import models as _tenancy_models  # noqa: E402, F401
from superaide.triage import models as _triage_models  # noqa: E402, F401
from superaide.trust import models as _trust_models  # noqa: E402, F401
