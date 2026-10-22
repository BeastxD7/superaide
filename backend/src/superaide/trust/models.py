"""Tables for the learning-and-trust features (issue #142). Each is per agent instance (`Scoped`) with RLS."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, CheckConstraint, Date, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
