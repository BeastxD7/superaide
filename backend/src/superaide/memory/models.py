"""Tables of `memory/` (ADR 0015 phase 7): moved out of platform/db/models.py unchanged."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
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
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class Context(Scoped, Base):
    __tablename__ = "context"
    __table_args__ = (
        UniqueConstraint("agent_id", "provider", "tenant_scope", "external_id", name="uq_context_key"),
        Index("ix_context_activity", "agent_id", text("last_event_at DESC"), text("id DESC")),
    )

    provider: Mapped[str] = mapped_column(String(20))
    tenant_scope: Mapped[str] = mapped_column(String(100), default="")
    external_id: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))  # dm | group | thread | meeting
    audience_class: Mapped[str] = mapped_column(String(30))  # private_1to1 | team | mixed_external
    title: Mapped[str] = mapped_column(String(200), default="")
    # When the newest event landed (or the context was made, while it has none). Maintained in one place,
    # memory.store.touch_context, so the console pages conversations by activity without a GROUP BY (ADR 0014).
    last_event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=func.now()
    )


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
        # Keyset paging of a conversation, newest first (ADR 0014): the order /app/messages and /api/events sort by.
        Index("ix_event_context_created", "context_id", text("created_at DESC"), text("id DESC")),
        Index("ix_event_agent_created", "agent_id", text("created_at DESC"), text("id DESC")),
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


class MemoryItem(Scoped, Base):
    """A remembered fact, tagged with the audience it may be shown to."""

    __tablename__ = "memory_item"
    __table_args__ = (
        Index("ix_memory_item_fts", text("to_tsvector('english', content)"), postgresql_using="gin"),
        # Operator paging of every memory, newest first (ADR 0014).
        Index("ix_memory_item_agent_created", "agent_id", text("created_at DESC"), text("id DESC")),
        # The owner's notebook: only live, undisputed notes, so forgotten and superseded history does not bloat it.
        Index(
            "ix_memory_item_live_created",
            "agent_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("valid_to IS NULL AND conflict_status = 'none'"),
        ),
    )

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
    # Further documents that say the same thing: erasing one keeps the fact while another still supports it.
    source_document_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list, server_default="{}"
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0, server_default="1")
    # The import lease this card came in under (imports/lease.py, #133); ending the lease deletes the row.
    lease_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("import_lease.id"), nullable=True, index=True)
    # fact | hearsay (#137): hearsay came from outside the owner and has fewer than two independent roots.
    belief_state: Mapped[str] = mapped_column(String(10), default="fact", server_default="fact")
    # Set when the fact came in with a memory passport: erasing that import deletes it (passport/importer.py).
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_import.id", ondelete="SET NULL"), nullable=True, index=True
    )

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
    # The owner locked this fact (Glass brain, #55): nothing automatic retires, merges or fades it. Only the owner can
    # change it (correct, forget, unlock); a newer claim against it waits as a conflict for the owner to decide.
    locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # none | pending (a weaker claim against a stronger fact) | review (a link fact outside about/work) | resolved
    conflict_status: Mapped[str] = mapped_column(String(10), default="none", server_default="none")
    conflicts_with_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("memory_item.id"), nullable=True)
    # The calendar event or email a fact was read from ("calendar:<id>", "gmail:<id>"); only its id, never its text.
    source_ref: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    # The passage of the document this fact was read from (the owner can click through to it).
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_chunk.id", ondelete="SET NULL"), nullable=True
    )


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


class MemoryImport(Scoped, Base):
    """One memory passport the owner imported: what came in, so it can be previewed and erased as a set."""

    __tablename__ = "memory_import"

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    label: Mapped[str] = mapped_column(String(200), default="")  # the file name the owner chose
    assistant: Mapped[str] = mapped_column(String(100), default="")  # what made the passport, from its manifest
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    fact_count: Mapped[int] = mapped_column(Integer, default=0)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    people_count: Mapped[int] = mapped_column(Integer, default=0)
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_import.id", ondelete="SET NULL"), nullable=True, index=True
    )


class DocumentChunk(Scoped, Base):
    """One passage of a Document's text. Visible only to whoever may see the document (its owner):
    the gate joins through `document`, so a chunk can't drift from its document's visibility."""

    __tablename__ = "document_chunk"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_document_chunk_ordinal"),
        Index("ix_document_chunk_fts", text("to_tsvector('english', text)"), postgresql_using="gin"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


class Contact(Scoped, Base):
    """Someone who matters to the owner. They are a Person too, so the agent can
    recognise them once their Telegram, Slack or email is linked."""

    __tablename__ = "contact"
    __table_args__ = (
        UniqueConstraint("owner_person_id", "person_id", name="uq_contact_owner_person"),
        # The owner's people list (VIPs first), only the contacts still kept (ADR 0014).
        Index(
            "ix_contact_owner_active",
            "owner_person_id",
            text("vip DESC"),
            postgresql_where=text("removed_at IS NULL"),
        ),
    )

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"))
    relationship: Mapped[str] = mapped_column(String(30), default="other")  # family | partner | boss | team | ...
    vip: Mapped[bool] = mapped_column(Boolean, default=False)  # can always get through, even in quiet hours
    notes: Mapped[str] = mapped_column(Text, default="")
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_import.id", ondelete="SET NULL"), nullable=True, index=True
    )


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


class Onboarding(Scoped, Base):
    """Where the person is in the agent's onboarding conversation."""

    __tablename__ = "onboarding"

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), unique=True)
    step: Mapped[str] = mapped_column(String(30), default="hello")
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
