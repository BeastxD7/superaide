"""Spend ledger with a hard cap.

Every model call reserves its worst-case cost first. The reservation is
refused if settled spend plus open reservations plus this call would pass the
cap, so total spend cannot exceed MAX_SPEND_USD (default US$10). After the call
the reservation is settled at the real price from the API's usage numbers.
The kill switch is checked here too, so a frozen agent makes no model calls.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select, text

from superaide.audit import audit
from superaide.brain.pricing import DEFAULT_PRICES, PriceRegistry, UsageCounts, cost_usd
from superaide.db.models import SpendEntry
from superaide.db.session import session_scope
from superaide.policy.killswitch import is_frozen

_LOCK_KEY = 784_221_001  # pg advisory lock id for the ledger


class BudgetExceeded(RuntimeError):
    def __init__(self, message: str, period: str = "lifetime") -> None:
        super().__init__(message)
        self.period = period  # lifetime | day | month | bucket


# Which daily bucket each kind of model call draws from.
BUCKETS = {"chat_turn": "chat", "verify": "chat", "intro_facts": "memory", "web_research": "research"}


def bucket_for(purpose: str) -> str:
    return BUCKETS.get(purpose, "other")


@dataclass(frozen=True)
class Limits:
    daily_usd: Decimal | None = None
    monthly_usd: Decimal | None = None
    daily_by_bucket: dict[str, Decimal] | None = None

    @classmethod
    def from_settings(cls, settings: object) -> Limits:
        def dec(v: float | None) -> Decimal | None:
            return None if v is None or v <= 0 else Decimal(str(v))

        by_bucket = getattr(settings, "budget_daily_by_bucket", {}) or {}
        return cls(
            daily_usd=dec(getattr(settings, "budget_daily_usd", None)),
            monthly_usd=dec(getattr(settings, "budget_monthly_usd", None)),
            daily_by_bucket={k: Decimal(str(v)) for k, v in by_bucket.items() if v and v > 0},
        )


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
    def __init__(
        self,
        cap_usd: float,
        db_url: str | None = None,
        limits: Limits | None = None,
        prices: PriceRegistry | None = None,
    ) -> None:
        self.cap = Decimal(str(cap_usd))
        self.db_url = db_url
        self.limits = limits or Limits()
        self.prices = prices or DEFAULT_PRICES

    @staticmethod
    def _committed(s, since: datetime | None = None, purposes: list[str] | None = None) -> Decimal:
        """Settled cost plus open reservations, optionally within a window or for some purposes."""
        amount = case(
            (SpendEntry.status == "settled", SpendEntry.cost_usd),
            (SpendEntry.status == "reserved", SpendEntry.reserved_usd),
            else_=0,
        )
        q = select(func.coalesce(func.sum(amount), 0))
        if since is not None:
            q = q.where(SpendEntry.created_at >= since)
        if purposes is not None:
            q = q.where(SpendEntry.purpose.in_(purposes))
        return Decimal(s.scalar(q) or 0)

    def _period_refusal(self, s, purpose: str, worst: Decimal) -> BudgetExceeded | None:
        now = datetime.now(UTC)
        day, month = now - timedelta(hours=24), now - timedelta(days=30)
        checks: list[tuple[str, Decimal | None, Decimal, str]] = [
            ("day", self.limits.daily_usd, self._committed(s, day), "today's"),
            ("month", self.limits.monthly_usd, self._committed(s, month), "this month's"),
        ]
        bucket = bucket_for(purpose)
        bucket_cap = (self.limits.daily_by_bucket or {}).get(bucket)
        if bucket_cap is not None:
            members = [p for p, b in BUCKETS.items() if b == bucket] or [purpose]
            checks.append(("bucket", bucket_cap, self._committed(s, day, members), f"today's {bucket}"))
        for period, cap, used, label in checks:
            if cap is not None and used + worst > cap:
                audit(
                    s, "budget", "llm_call_refused", reason=period, purpose=purpose, bucket=bucket,
                    committed_usd=float(used), request_usd=float(worst), cap_usd=float(cap),
                )  # fmt: skip
                return BudgetExceeded(f"{label} limit of US${cap} would be passed", period)
        return None

    def summary(self) -> SpendSummary:
        with session_scope(self.db_url) as s:
            settled = s.scalar(
                select(func.coalesce(func.sum(SpendEntry.cost_usd), 0)).where(SpendEntry.status == "settled")
            )
            reserved = s.scalar(
                select(func.coalesce(func.sum(SpendEntry.reserved_usd), 0)).where(SpendEntry.status == "reserved")
            )
            calls = s.scalar(select(func.count()).select_from(SpendEntry).where(SpendEntry.status == "settled"))
        return SpendSummary(self.cap, Decimal(settled or 0), Decimal(reserved or 0), int(calls or 0))

    def reserve(self, *, purpose: str, model: str, worst_case_usd: Decimal, provider: str = "anthropic") -> uuid.UUID:
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
                committed = Decimal(settled or 0) + Decimal(reserved or 0)
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
                elif (period_refusal := self._period_refusal(s, purpose, worst_case_usd)) is not None:
                    refusal = period_refusal
                else:
                    entry = SpendEntry(
                        provider=provider, purpose=purpose, model=model, status="reserved", reserved_usd=worst_case_usd
                    )
                    s.add(entry)
                    s.flush()
                    entry_id = entry.id
        # Raise only after the audit row is committed.
        if refusal is not None:
            raise refusal
        return entry_id

    def settle(
        self,
        entry_id: uuid.UUID,
        *,
        model: str,
        usage: UsageCounts,
        request_id: str | None,
        batch: bool = False,
        provider: str = "anthropic",
        reported_cost_usd: Decimal | None = None,
        known: bool = True,
    ) -> Decimal:
        """Settle at the real price. With no usage from the provider (`known=False`) the call is
        settled at its reservation, never at 0. When the provider reports what it charged, the
        higher of that and our own figure counts, and a gap of more than 5% is audited."""
        computed = cost_usd(model, usage, batch=batch, provider=provider, prices=self.prices) if known else None
        with session_scope(self.db_url) as s:
            entry = s.get(SpendEntry, entry_id)
            assert entry is not None
            if computed is None:
                cost = entry.reserved_usd
                audit(s, "budget", "usage_missing", provider=provider, model=model, cost_usd=float(cost))
            else:
                cost = computed
                if reported_cost_usd is not None:
                    gap = abs(reported_cost_usd - computed)
                    if gap > max(computed, reported_cost_usd) * Decimal("0.05"):
                        audit(
                            s, "budget", "cost_mismatch", provider=provider, model=model,
                            computed_usd=float(computed), reported_usd=float(reported_cost_usd),
                        )  # fmt: skip
                    cost = max(computed, reported_cost_usd)
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
