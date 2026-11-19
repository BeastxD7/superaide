"""`commitment`: a promise with a person and a time (#480). Per agent instance (`Scoped`), under Row-Level Security.

"Tell Meera I'll reply in an hour" becomes a row: who owes it, to whom, by when, and where it stands. Code writes the
rows, from what the owner typed or did (the relay she approved, the app); the model never creates, closes or moves one.
Owner-private: nothing here is shown to anyone else or put in a prompt for anyone but the owner. Nothing is ever
deleted: closing sets a state and a time, removing sets `removed_at` (retention keeps everything)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped

# proposed (waiting for the owner to confirm) | open | overdue (past due, nobody closed it) | escalated (one more
# reminder went out after it was overdue) | done | cancelled
STATES = ("proposed", "open", "overdue", "escalated", "done", "cancelled")
ACTIVE = ("proposed", "open", "overdue", "escalated")
CLOSED = ("done", "cancelled")


def _now() -> datetime:
    return datetime.now(UTC)


class Commitment(Scoped, Base):
    __tablename__ = "commitment"
    __table_args__ = (
        Index("ix_commitment_owner_state_due", "agent_id", "owner_person_id", "state", "due_at", "id"),
        Index("ix_commitment_context", "context_id", "state"),
        # One commitment per approved relay, whatever happens twice.
        Index(
            "uq_commitment_source_approval",
            "source_approval_id",
            unique=True,
            postgresql_where=text("source_approval_id IS NOT NULL"),
        ),
    )

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"))
    debtor: Mapped[str] = mapped_column(String(8), default="owner")  # owner | ava: who has to do it
    # To whom: the conversation of someone Ava does not know (identity and DM context), or only a label.
    creditor_identity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("channel_identity.id"), nullable=True)
    context_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("context.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(20), default="")
    creditor_label: Mapped[str] = mapped_column(String(60), default="")  # a first name, display only
    text: Mapped[str] = mapped_column(Text, default="")  # the owner's own words
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(12), default="open")
    source: Mapped[str] = mapped_column(String(10), default="owner")  # relay | owner
    source_approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approval.id"), nullable=True)
    nudged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    overdue_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[str] = mapped_column(String(10), default="")  # "" | owner | reply | system
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # soft delete
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
