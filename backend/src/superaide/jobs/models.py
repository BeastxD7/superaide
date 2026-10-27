"""Tables of `jobs/` (ADR 0015 phase 7): moved out of platform/db/models.py unchanged."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


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


# The claim function is created right after this table (create_all, tests, init-db): importing it registers that.
from superaide.jobs import claim as _claim  # noqa: E402, F401
