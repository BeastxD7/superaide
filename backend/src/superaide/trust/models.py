"""Tables for the learning-and-trust features (issue #142). Each is per agent instance (`Scoped`) with RLS."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    DDL,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from superaide.db.models import Base, Scoped


class OwnerQuestion(Scoped, Base):
    """The one clarifying question Ava may ask the owner on a given local day (#121).

    The unique key (person, day) is the daily budget: a second question on the same day cannot be stored.
    """

    __tablename__ = "owner_question"
    __table_args__ = (
        UniqueConstraint("person_id", "asked_on", name="uq_owner_question_day"),
        CheckConstraint("status in ('asked', 'answered', 'skipped')", name="ck_owner_question_status"),
    )

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    asked_on: Mapped[date] = mapped_column(Date)  # the owner's local day
    topic: Mapped[str] = mapped_column(String(40))
    question: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float, default=0.0)  # uncertainty x impact when it was chosen
    status: Mapped[str] = mapped_column(String(10), default="asked")
    answer: Mapped[str] = mapped_column(Text, default="")
    memory_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_item.id", ondelete="SET NULL"), nullable=True
    )
    selected_by: Mapped[str] = mapped_column(String(20), default="uncertainty")  # uncertainty | random (experiment)


class NeverAskTopic(Scoped, Base):
    """A topic the owner never wants to be asked about."""

    __tablename__ = "never_ask_topic"
    __table_args__ = (UniqueConstraint("person_id", "topic", name="uq_never_ask_topic"),)

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    topic: Mapped[str] = mapped_column(String(40))


class MemoryInfluence(Scoped, Base):
    """How much removing one memory changed a reply (#122): 0 = identical answer, 1 = nothing in common."""

    __tablename__ = "memory_influence"
    __table_args__ = (UniqueConstraint("event_id", "memory_id", name="uq_memory_influence"),)

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event.id", ondelete="CASCADE"), index=True)
    memory_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("memory_item.id", ondelete="CASCADE"), index=True)
    influence: Mapped[float] = mapped_column(Float)
    changed: Mapped[bool] = mapped_column(Boolean)
    ablations: Mapped[int] = mapped_column(Integer, default=1)  # how many ablation runs this score averages


class AgreementLog(Scoped, Base):
    """One reply that judged the owner's own claim or choice (#123): did it side with them?"""

    __tablename__ = "agreement_log"

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    validated: Mapped[bool] = mapped_column(Boolean)  # the reply sided with the owner
    second_stance: Mapped[str] = mapped_column(String(10))  # the memory-free reader: agree | disagree | unsure
    flagged: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # reply validated but the memory-free reader disagreed


class EmotionalUse(Scoped, Base):
    """One emotionally heavy or "what are you really?" turn (#124): when and which kind, never the words."""

    __tablename__ = "emotional_use"

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # crisis | heavy | meta


MAX_ACTIVE_PLAYBOOKS = 50


class Playbook(Scoped, Base):
    """A reusable how-to ("how Shashank wants board updates") Ava drafts and only the owner can switch on (#120)."""

    __tablename__ = "playbook"
    __table_args__ = (
        # Only an owner-approved playbook may be active: the database refuses anything else.
        CheckConstraint("status in ('draft', 'active', 'retired')", name="ck_playbook_status"),
        CheckConstraint("status <> 'active' OR approved_by_person_id IS NOT NULL", name="ck_playbook_active_approved"),
    )

    owner_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    drafted_body: Mapped[str] = mapped_column(Text)  # what Ava wrote; never changes
    body: Mapped[str] = mapped_column(Text)  # what runs: the owner's edit
    tools: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(10), default="draft")
    approved_by_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    retired_reason: Mapped[str] = mapped_column(String(200), default="")
    # The coin-flip A/B (outcomes are edit distance: how much the owner changed Ava's draft, 0 to 1).
    used_n: Mapped[int] = mapped_column(Integer, default=0)
    used_dist: Mapped[float] = mapped_column(Float, default=0.0)
    unused_n: Mapped[int] = mapped_column(Integer, default=0)
    unused_dist: Mapped[float] = mapped_column(Float, default=0.0)


# A hard cap of 50 active playbooks per Ava, enforced by the database (not by the code that activates them).
event.listen(
    Playbook.__table__,
    "after_create",
    DDL(
        f"""
CREATE OR REPLACE FUNCTION playbook_active_cap() RETURNS trigger AS $$
BEGIN
  IF NEW.status = 'active' AND (SELECT count(*) FROM playbook
       WHERE agent_id = NEW.agent_id AND status = 'active' AND id <> NEW.id) >= {MAX_ACTIVE_PLAYBOOKS} THEN
    RAISE EXCEPTION 'at most {MAX_ACTIVE_PLAYBOOKS} active playbooks';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER playbook_active_cap BEFORE INSERT OR UPDATE ON playbook
  FOR EACH ROW EXECUTE FUNCTION playbook_active_cap();
"""
    ).execute_if(dialect="postgresql"),
)


class PromptVersion(Scoped, Base):
    """One version of a prompt Ava may tune (#118). Only the owner moves a version to `active`."""

    __tablename__ = "prompt_version"
    __table_args__ = (UniqueConstraint("agent_id", "key", "version", name="uq_prompt_version"),)

    key: Mapped[str] = mapped_column(String(40))  # one of trust.surface.WRITABLE_PROMPTS
    version: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # proposed (waiting for the owner) | rejected (failed a gate, or the owner said no) | active | superseded
    status: Mapped[str] = mapped_column(String(12), default="proposed")
    base_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # held-out accuracy of the parent
    cand_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # held-out accuracy of this version
    held_out: Mapped[int] = mapped_column(Integer, default=0)  # how many held-out examples scored both
    invariants: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {"ok": bool, "failed": [...]}
    rationale: Mapped[str] = mapped_column(Text, default="")
    decided_by_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
