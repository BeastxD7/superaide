"""Spend ledger with a hard cap, per agent instance, plus a platform-wide guard.

Every model call reserves its worst-case cost first. The reservation is
refused if settled spend plus open reservations plus this call would pass the
cap, so total spend cannot exceed MAX_SPEND_USD (default US$10). After the call
the reservation is settled at the real price from the API's usage numbers.
The kill switch is checked here too, so a frozen agent makes no model calls.

Many owners (ADR 0009 T6):
- The cap and the rolling limits count only this instance's ledger, and the lock
  that keeps reservations honest is per instance, so owners never wait on each other.
- An instance may carry its own limits (`agent_instance.spend_limits`), over the
  .env defaults.
- When the platform's own model key pays (`key_source="platform"`), each call also
  draws on a platform-wide daily allowance (`platform_spend`, one row per UTC day),
  reserved with one atomic UPDATE, no global lock.
- Who pays decides which limits count. The lifetime cap (MAX_SPEND_USD) counts every row, whoever
  paid, so no instance can spend more than it. The owner's day, month and bucket limits count only
  what their own key paid. Superaide AI credits answer to their own limits instead (the owner's
  credits allowance and the platform's daily cap), so using up the credits never stops the owner's
  own key, and the owner's own limits never cut the credits short of the allowance the card shows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select, text

from superaide.audit import audit
from superaide.brain import platform_spend as platform
from superaide.brain.pricing import DEFAULT_PRICES, PriceRegistry, UsageCounts, cost_usd
from superaide.db.models import SpendEntry
from superaide.db.session import session_scope
from superaide.policy.killswitch import is_frozen
from superaide.tenancy.context import InstanceScope, scope_or_default, use_instance

_LOCK_KEY = 784_221_001  # pg advisory lock namespace for the ledger; the second key is the instance
KEY_SOURCES = ("owner", "platform")


class BudgetExceeded(RuntimeError):
    def __init__(self, message: str, period: str = "lifetime") -> None:
        super().__init__(message)
        self.period = period  # lifetime | day | month | bucket


# Which daily bucket each kind of model call draws from.
BUCKETS = {
    "chat_turn": "chat",
    "verify": "chat",
    "intro_facts": "memory",
    "web_research": "research",
    "triage": "triage",  # ADR 0010: the triage classifier, rules only once this is spent
    # ADR 0012: the introduction's voice call (the interviewer's turns, speech-to-text, voices)
    "voice_turn": "voice",
    "voice_asr": "voice",
    "voice_tts": "voice",
    "receptionist": "receptionist",  # ADR 0010 TR5: chats with people Ava doesn't know
    # Learning and trust features (#142): replay ablations and memory-free second opinions
    "replay": "learning",
    "second_opinion": "learning",
    # Learning and trust features (#142)
    "playbook": "learning",
    # Learning and trust features (#142)
    "tuner": "learning",
    "rule_compile": "rules",  # #52: turning a standing order into a structured rule (one small call)
}


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


def _override(cap: Decimal, limits: Limits, own: dict[str, Any]) -> tuple[Decimal, Limits]:
    """Apply an instance's own limits; values that aren't positive numbers are ignored."""

    def positive(value: Any) -> Decimal | None:
        try:
            d = Decimal(str(value))
        except ArithmeticError, ValueError:
            return None
        return d if d.is_finite() and d > 0 else None

    by_bucket = dict(limits.daily_by_bucket or {})
    if isinstance(own.get("daily_by_bucket"), dict):
        for bucket, value in own["daily_by_bucket"].items():
            if (d := positive(value)) is not None:
                by_bucket[str(bucket)] = d
    return (
        positive(own.get("lifetime_usd")) or cap,
        Limits(
            daily_usd=positive(own.get("daily_usd")) or limits.daily_usd,
            monthly_usd=positive(own.get("monthly_usd")) or limits.monthly_usd,
            daily_by_bucket=by_bucket,
        ),
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


STALE_RESERVATION = timedelta(minutes=10)  # a call that is still unsettled after this was lost to a crash


class BudgetGuard:
    def __init__(
        self,
        cap_usd: float,
        db_url: str | None = None,
        limits: Limits | None = None,
        prices: PriceRegistry | None = None,
        *,
        scope: InstanceScope | None = None,
        key_source: str = "owner",
    ) -> None:
        if key_source not in KEY_SOURCES:
            raise ValueError(f"key_source must be one of {KEY_SOURCES}")
        self.cap = Decimal(str(cap_usd))
        self.db_url = db_url
        self.limits = limits or Limits()
        # Superaide pays for credit calls, so they are priced from the built-in tables only: never from
        # a price the owner typed in (an owner price of 0 must not make Superaide's calls free).
        self.prices = DEFAULT_PRICES if key_source == "platform" else (prices or DEFAULT_PRICES)
        self.scope = scope or scope_or_default()  # the instance whose ledger this guards
        self.key_source = key_source

    @classmethod
    def for_instance(
        cls,
        settings: Any,
        *,
        prices: PriceRegistry | None = None,
        scope: InstanceScope | None = None,
        key_source: str = "owner",
    ) -> BudgetGuard:
        """The guard for one instance: the .env limits, overridden by the instance's own spend_limits.
        `key_source="platform"` is the guard for calls Superaide AI credits pay for (brain/platform_ai.py)."""
        scope = scope or scope_or_default()
        cap, limits = Decimal(str(settings.max_spend_usd)), Limits.from_settings(settings)
        from superaide.tenancy.models import AgentInstance

        with use_instance(scope), session_scope() as s:
            instance = s.get(AgentInstance, scope.agent_id)
            own = dict(instance.spend_limits or {}) if instance else {}
        cap, limits = _override(cap, limits, own)
        return cls(float(cap), limits=limits, prices=prices, scope=scope, key_source=key_source)

    def _mine(self) -> Any:
        return SpendEntry.agent_id == self.scope.agent_id

    def _committed(
        self,
        s,
        since: datetime | None = None,
        purposes: list[str] | None = None,
        *,
        all_sources: bool = False,
    ) -> Decimal:
        """Settled cost plus open reservations, optionally within a window or for some purposes.
        Counts what this guard's key source paid; `all_sources=True` counts everything (the lifetime cap)."""
        amount = case(
            (SpendEntry.status == "settled", SpendEntry.cost_usd),
            (SpendEntry.status == "reserved", SpendEntry.reserved_usd),
            else_=0,
        )
        q = select(func.coalesce(func.sum(amount), 0)).where(self._mine())
        if not all_sources:
            q = q.where(SpendEntry.key_source == self.key_source)
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

    def _credits_refusal(self, s, purpose: str, worst: Decimal) -> BudgetExceeded | None:
        """This owner's share of Superaide AI credits (rolling day and month), checked under the
        instance lock before the platform-wide draw."""
        from superaide.brain import platform_ai

        day_cap, month_cap = platform_ai.allowance()
        day, month = platform_ai.used(s, self.scope.agent_id)
        for period, cap, spent in (("platform_day", day_cap, day), ("platform_month", month_cap, month)):
            if cap is not None and spent + worst > cap:
                audit(
                    s, "budget", "llm_call_refused", reason=period, purpose=purpose, committed_usd=float(spent),
                    request_usd=float(worst), cap_usd=float(cap),
                )  # fmt: skip
                word = "daily" if period == "platform_day" else "monthly"
                return BudgetExceeded(f"Superaide AI credits: {word} allowance used", period)
        return None

    def _release_orphans(self, s) -> None:
        """Give back reservations that were never settled or failed: the process died between reserve()
        and settle(). They would otherwise count against the cap for good. Runs under the instance lock;
        a platform-paid one also returns its share of the platform's day total."""
        cutoff = datetime.now(UTC) - STALE_RESERVATION
        orphans = s.scalars(
            select(SpendEntry).where(self._mine(), SpendEntry.status == "reserved", SpendEntry.created_at < cutoff)
        ).all()
        for entry in orphans:
            if entry.key_source == "platform":
                platform.adjust(s, entry.created_at, -entry.reserved_usd)
            entry.status = "failed"
            entry.settled_at = datetime.now(UTC)
            audit(s, "budget", "reservation_released", purpose=entry.purpose, reserved_usd=float(entry.reserved_usd))

    def summary(self) -> SpendSummary:
        with use_instance(self.scope), session_scope(self.db_url) as s:
            settled = s.scalar(
                select(func.coalesce(func.sum(SpendEntry.cost_usd), 0)).where(
                    self._mine(), SpendEntry.status == "settled"
                )
            )
            reserved = s.scalar(
                select(func.coalesce(func.sum(SpendEntry.reserved_usd), 0)).where(
                    self._mine(), SpendEntry.status == "reserved"
                )
            )
            calls = s.scalar(
                select(func.count()).select_from(SpendEntry).where(self._mine(), SpendEntry.status == "settled")
            )
        return SpendSummary(self.cap, Decimal(settled or 0), Decimal(reserved or 0), int(calls or 0))

    def reserve(self, *, purpose: str, model: str, worst_case_usd: Decimal, provider: str = "anthropic") -> uuid.UUID:
        refusal: Exception | None = None
        with use_instance(self.scope), session_scope(self.db_url) as s:
            if is_frozen(s):
                audit(s, "budget", "llm_call_refused", reason="frozen", purpose=purpose)
                refusal = AgentFrozen("Kill switch is on; no model calls")
            else:
                # Serialise reservations for this instance only: other owners never wait on this one.
                s.execute(
                    text("SELECT pg_advisory_xact_lock(:ns, hashtext(:agent))"),
                    {"ns": _LOCK_KEY, "agent": str(self.scope.agent_id)},
                )
                self._release_orphans(s)
                committed = self._committed(s, all_sources=True)
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
                elif (
                    self.key_source == "owner"
                    and (period_refusal := self._period_refusal(s, purpose, worst_case_usd)) is not None
                ):
                    refusal = period_refusal  # the owner's own limits apply to the owner's own key only
                elif self.key_source == "platform" and (
                    owner_refusal := self._credits_refusal(s, purpose, worst_case_usd)
                ):
                    refusal = owner_refusal
                elif self.key_source == "platform" and not platform.draw(s, worst_case_usd):
                    audit(
                        s, "budget", "llm_call_refused", reason="platform", purpose=purpose,
                        request_usd=float(worst_case_usd),
                    )  # fmt: skip
                    refusal = BudgetExceeded("Superaide's shared model allowance for today is used up", "platform")
                else:
                    entry = SpendEntry(
                        provider=provider,
                        purpose=purpose,
                        model=model,
                        status="reserved",
                        reserved_usd=worst_case_usd,
                        key_source=self.key_source,
                        tenant_id=self.scope.tenant_id,
                        agent_id=self.scope.agent_id,
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
        usage: UsageCounts | None,
        request_id: str | None,
        batch: bool = False,
        provider: str = "anthropic",
        reported_cost_usd: Decimal | None = None,
        known: bool = True,
        fixed_cost_usd: Decimal | None = None,
    ) -> Decimal:
        """Settle at the real price. With no usage from the provider (`known=False`) the call is
        settled at its reservation, never at 0. When the provider reports what it charged, the
        higher of that and our own figure counts, and a gap of more than 5% is audited."""
        usage = usage or UsageCounts()
        if fixed_cost_usd is not None:  # a flat-priced call, e.g. one web search
            computed: Decimal | None = fixed_cost_usd
        else:
            computed = cost_usd(model, usage, batch=batch, provider=provider, prices=self.prices) if known else None
        with use_instance(self.scope), session_scope(self.db_url) as s:
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
            if entry.key_source == "platform":
                platform.adjust(s, entry.created_at, cost - entry.reserved_usd)
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
        with use_instance(self.scope), session_scope(self.db_url) as s:
            entry = s.get(SpendEntry, entry_id)
            if entry is not None and entry.status == "reserved" and entry.key_source == "platform":
                platform.adjust(s, entry.created_at, -entry.reserved_usd)
            if entry is not None:
                entry.status = "failed"
                entry.settled_at = datetime.now(UTC)
            audit(s, "budget", "llm_call_failed", error=error[:500])
