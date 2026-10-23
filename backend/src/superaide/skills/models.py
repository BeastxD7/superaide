"""Tables for skill trust signals (issue #130). Counters and verdicts only: no arguments, no content."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from superaide.db.models import Base, Scoped


class SkillScan(Scoped, Base):
    """One scanner's verdict on one version of a skill. Several scanners write several rows; the ledger
    (skills/signals.py) reads the pattern across them."""

    __tablename__ = "skill_scan"
    __table_args__ = (UniqueConstraint("agent_id", "skill_id", "version", "scanner", name="uq_skill_scan"),)

    skill_id: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[str] = mapped_column(String(40))
    scanner: Mapped[str] = mapped_column(String(40))
    verdict: Mapped[str] = mapped_column(String(12))  # clean | suspicious | malicious
    reasons: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # short rule codes, never skill text
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    human_concern: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # a reviewer's label, for the experiment


class SkillCounter(Scoped, Base):
    """A counter in the telemetry allowlist (skills/signals.py ALLOWED_KINDS): calls, errors, reverts, the effect
    classes called and the host names contacted. Never an argument, a result or a message."""

    __tablename__ = "skill_counter"
    __table_args__ = (UniqueConstraint("agent_id", "skill_id", "version", "kind", "key", name="uq_skill_counter"),)

    skill_id: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(12))  # call | error | revert | effect | host
    key: Mapped[str] = mapped_column(String(120), default="total")
    count: Mapped[int] = mapped_column(Integer, default=0)


class SkillDemotion(Scoped, Base):
    """A skill version taken out of service because what it did did not match its manifest."""

    __tablename__ = "skill_demotion"
    __table_args__ = (UniqueConstraint("agent_id", "skill_id", "version", name="uq_skill_demotion"),)

    skill_id: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[str] = mapped_column(String(40))
    gaps: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # e.g. "effect:send", "host:evil.example"
    demoted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SkillReceipt(Scoped, Base):
    """One signed-off outcome of one skill action, in an append-only hash chain (skills/receipts.py, issue #131).
    No content, no names: a salted per-skill owner reference, the skill version, the effect class and the outcome."""

    __tablename__ = "skill_receipt"
    __table_args__ = (UniqueConstraint("agent_id", "seq", name="uq_skill_receipt_seq"),)

    seq: Mapped[int] = mapped_column(Integer)
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64))
    skill_id: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[str] = mapped_column(String(40))
    effect_class: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[str] = mapped_column(String(20))  # approved_unedited | edited | rejected | reverted
    owner_ref: Mapped[str] = mapped_column(String(32))
    ref_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)  # a revert points at the receipt it undoes
    latency_s: Mapped[int] = mapped_column(Integer, default=0)  # seconds between the request and the decision
    batch: Mapped[bool] = mapped_column(Boolean, default=False)  # decided together with others
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PayoutEntry(Scoped, Base):
    """One line of the creator payout ledger (skills/payouts.py, issue #132). Whole minor units (cents), never a float.
    Append-only in practice: a mistake is fixed by a new line, not an edit. Nothing here moves money."""

    __tablename__ = "payout_entry"
    __table_args__ = (UniqueConstraint("agent_id", "kind", "receipt_seq", name="uq_payout_entry_receipt"),)

    kind: Mapped[str] = mapped_column(String(16))  # accrual | clawback | bond_deposit | bond_refund | settlement
    creator: Mapped[str] = mapped_column(String(80), index=True)
    skill_id: Mapped[str] = mapped_column(String(120), default="")
    version: Mapped[str] = mapped_column(String(40), default="")
    receipt_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gross_minor: Mapped[int] = mapped_column(Integer, default=0)
    fee_minor: Mapped[int] = mapped_column(Integer, default=0)
    amount_minor: Mapped[int] = mapped_column(Integer)  # signed: the effect on the creator's balance (payable or bond)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # an accrual is payable only from here
    note: Mapped[str] = mapped_column(String(200), default="")
