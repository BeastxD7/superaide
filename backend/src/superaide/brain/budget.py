"""Spend ledger with a hard cap.

Every Claude call reserves its worst-case cost first. The reservation is
refused if settled spend plus open reservations plus this call would pass the
cap, so total spend cannot exceed MAX_SPEND_USD (default US$10). After the call
the reservation is settled at the real price from the API's usage numbers.
The kill switch is checked here too, so a frozen agent makes no model calls.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select, text

from superaide.audit import audit
from superaide.brain.pricing import UsageCounts, cost_usd
from superaide.db.models import SpendEntry
from superaide.db.session import session_scope
from superaide.policy.killswitch import is_frozen

_LOCK_KEY = 784_221_001  # pg advisory lock id for the ledger


class BudgetExceeded(RuntimeError):
    pass


class AgentFrozen(RuntimeError):
    pass


@dataclass
class SpendSummary:
    cap_usd: Decimal
    settled_usd: Decimal
    reserved_usd: Decimal
    calls: int

    @property
    def remaining_usd(self) -> Decimal:
        return max(Decimal(0), self.cap_usd - self.settled_usd - self.reserved_usd)

    def as_dict(self) -> dict[str, float | int]:
        return {
            "cap_usd": float(self.cap_usd),
            "settled_usd": float(self.settled_usd),
            "reserved_usd": float(self.reserved_usd),
            "remaining_usd": float(self.remaining_usd),
            "calls": self.calls,
        }


class BudgetGuard:
    def __init__(self, cap_usd: float, db_url: str | None = None) -> None:
        self.cap = Decimal(str(cap_usd))
        self.db_url = db_url

    def summary(self) -> SpendSummary:
        with session_scope(self.db_url) as s:
            settled = s.scalar(
                select(func.coalesce(func.sum(SpendEntry.cost_usd), 0)).where(SpendEntry.status == "settled")
            )
            reserved = s.scalar(
                select(func.coalesce(func.sum(SpendEntry.reserved_usd), 0)).where(SpendEntry.status == "reserved")
            )
            calls = s.scalar(select(func.count()).select_from(SpendEntry).where(SpendEntry.status == "settled"))
        return SpendSummary(self.cap, Decimal(settled), Decimal(reserved), int(calls or 0))

    def reserve(self, *, purpose: str, model: str, worst_case_usd: Decimal) -> uuid.UUID:
        refusal: Exception | None = None
        with session_scope(self.db_url) as s:
            if is_frozen(s):
                audit(s, "budget", "llm_call_refused", reason="frozen", purpose=purpose)
                refusal = AgentFrozen("Kill switch is on; no model calls")
            else:
                s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
                settled = s.scalar(
                    select(func.coalesce(func.sum(SpendEntry.cost_usd), 0)).where(SpendEntry.status == "settled")
                )
                reserved = s.scalar(
                    select(func.coalesce(func.sum(SpendEntry.reserved_usd), 0)).where(SpendEntry.status == "reserved")
                )
                committed = Decimal(settled) + Decimal(reserved)
                if committed + worst_case_usd > self.cap:
                    audit(
                        s,
                        "budget",
                        "llm_call_refused",
                        reason="cap",
                        purpose=purpose,
                        committed_usd=float(committed),
                        request_usd=float(worst_case_usd),
                        cap_usd=float(self.cap),
                    )
                    refusal = BudgetExceeded(
                        f"Spend cap US${self.cap} would be passed: committed US${committed:.4f}, "
                        f"this call up to US${worst_case_usd:.4f}"
                    )
                else:
                    entry = SpendEntry(purpose=purpose, model=model, status="reserved", reserved_usd=worst_case_usd)
                    s.add(entry)
                    s.flush()
                    entry_id = entry.id
        # Raise only after the audit row is committed.
        if refusal is not None:
            raise refusal
        return entry_id

    def settle(
        self, entry_id: uuid.UUID, *, model: str, usage: UsageCounts, request_id: str | None, batch: bool = False
    ) -> Decimal:
        cost = cost_usd(model, usage, batch=batch)
        with session_scope(self.db_url) as s:
            entry = s.get(SpendEntry, entry_id)
            assert entry is not None
            entry.status = "settled"
            entry.cost_usd = cost
            entry.input_tokens = usage.input_tokens
            entry.output_tokens = usage.output_tokens
            entry.cache_read_tokens = usage.cache_read_tokens
            entry.cache_write_tokens = usage.cache_write_5m_tokens + usage.cache_write_1h_tokens
            entry.request_id = request_id
            entry.settled_at = datetime.now(UTC)
            audit(s, "budget", "llm_call_settled", purpose=entry.purpose, model=model, cost_usd=float(cost))
            if cost > entry.reserved_usd:
                # The estimate was too low; the cap could be passed by this difference.
                audit(s, "budget", "reservation_exceeded", reserved_usd=float(entry.reserved_usd), cost_usd=float(cost))
        return cost

    def fail(self, entry_id: uuid.UUID, error: str) -> None:
        with session_scope(self.db_url) as s:
            entry = s.get(SpendEntry, entry_id)
            if entry is not None:
                entry.status = "failed"
                entry.settled_at = datetime.now(UTC)
            audit(s, "budget", "llm_call_failed", error=error[:500])
