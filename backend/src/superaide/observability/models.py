"""Tables of `observability/`: one row of numbers per message turn (#412)."""

from __future__ import annotations

from sqlalchemy import Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class TurnTiming(Scoped, Base):
    """How long each stage of one turn took, in milliseconds.

    Numbers and two short words (the channel and the outcome) only: no message text, no names, no ids of people.
    A stage the turn never reached is NULL, not 0, so it does not pull a percentile down.
    """

    __tablename__ = "turn_timing"
    __table_args__ = (Index("ix_turn_timing_agent_created", "agent_id", text("created_at DESC"), text("id DESC")),)

    provider: Mapped[str] = mapped_column(String(16))  # web | telegram | slack | ...
    mode: Mapped[str] = mapped_column(String(16))  # known | stranger | unconfirmed | halted | ...
    total_ms: Mapped[int] = mapped_column(Integer)
    verify_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    identity_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gate_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieval_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    airlock_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reply_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    send_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
