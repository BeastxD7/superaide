"""ADR 0009 T6: each hired Ava has its own budget; the platform's own key has a shared daily allowance."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from superaide.app import accounts
from superaide.brain.models import SpendEntry
from superaide.brain.spend import platform_spend
from superaide.brain.spend.budget import BudgetExceeded, BudgetGuard, Limits
from superaide.brain.spend.pricing import UsageCounts
from superaide.identity.auth.service import Client, signup
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import InstanceScope, default_scope, use_instance
from superaide.platform.tenancy.models import AgentInstance, PlatformSpend

A = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
B = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
CENT = Decimal("0.01")


def guard(scope: InstanceScope, cap: float = 0.05, **kw) -> BudgetGuard:
    return BudgetGuard(cap, limits=Limits(), scope=scope, **kw)


def spend(g: BudgetGuard, amount: Decimal = CENT) -> uuid.UUID:
    return g.reserve(purpose="chat_turn", model="claude-haiku-5-5", worst_case_usd=amount)


def test_one_owner_reaching_their_cap_does_not_stop_another():
    a, b = guard(A), guard(B)
    for _ in range(5):
        spend(a)
    with pytest.raises(BudgetExceeded):
        spend(a)
    spend(b)  # B's ledger is its own
    assert a.summary().reserved_usd == Decimal("0.05") and b.summary().reserved_usd == CENT


def test_ledger_rows_belong_to_the_guards_instance_whatever_is_in_scope():
    with use_instance(B):
        entry_id = spend(guard(A))
    with session_scope() as s:
        entry = s.get(SpendEntry, entry_id)
        assert entry is not None and (entry.agent_id, entry.tenant_id, entry.key_source) == (
            A.agent_id,
            A.tenant_id,
            "owner",
        )


def test_rolling_limits_are_per_instance_too():
    limits = Limits(daily_usd=Decimal("0.02"))
    a = BudgetGuard(10, limits=limits, scope=A)
    b = BudgetGuard(10, limits=limits, scope=B)
    spend(a), spend(a)
    with pytest.raises(BudgetExceeded) as exc:
        spend(a)
    assert exc.value.period == "day"
    spend(b)


def test_reservations_for_different_owners_run_side_by_side():
    errors: list[BaseException] = []

    def burst(scope: InstanceScope) -> None:
        try:
            g = guard(scope, cap=1)
            for _ in range(10):
                g.settle(spend(g), model="claude-haiku-5-5", usage=UsageCounts(input_tokens=10), request_id=None)
        except BaseException as exc:  # noqa: BLE001 (reported below)
            errors.append(exc)

    threads = [threading.Thread(target=burst, args=(s,)) for s in (A, B, A, B)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert guard(A, cap=1).summary().calls == guard(B, cap=1).summary().calls == 20


# --- an instance's own limits ----------------------------------------------------------------


def _hired_instance(limits: dict) -> InstanceScope:
    with session_scope() as s:
        user, _ = signup(s, email="s@example.com", password="correct horse battery", name="Jordan", client=Client())
        accounts.hire(s, user)
        instance = s.get(AgentInstance, default_scope().agent_id)
        assert instance is not None
        instance.spend_limits = limits
    return default_scope()


def test_an_instance_can_carry_its_own_limits():
    scope = _hired_instance({"lifetime_usd": 0.02, "daily_usd": "0.03", "daily_by_bucket": {"chat": 0.015}})
    g = BudgetGuard.for_instance(get_settings(), scope=scope)
    assert g.cap == Decimal("0.02")
    assert g.limits.daily_usd == Decimal("0.03")
    assert (g.limits.daily_by_bucket or {})["chat"] == Decimal("0.015")
    assert (g.limits.daily_by_bucket or {})["memory"] == Decimal("0.15")  # the default still applies


@pytest.mark.parametrize("bad", [{"lifetime_usd": -1}, {"lifetime_usd": "lots"}, {"daily_usd": 0}, {"x": 1}])
def test_nonsense_instance_limits_fall_back_to_the_defaults(bad):
    g = BudgetGuard.for_instance(get_settings(), scope=_hired_instance(bad))
    settings = get_settings()
    assert g.cap == Decimal(str(settings.max_spend_usd))
    assert g.limits.daily_usd == Decimal(str(settings.budget_daily_usd))


def test_without_an_instance_row_the_defaults_apply():
    g = BudgetGuard.for_instance(get_settings(), scope=A)
    assert (g.cap, g.key_source) == (Decimal(str(get_settings().max_spend_usd)), "owner")


# --- the platform's own key -------------------------------------------------------------------


@pytest.fixture
def platform_cap(monkeypatch):
    def set_cap(value: float) -> None:
        monkeypatch.setattr(get_settings(), "platform_daily_cap_usd", value)

    return set_cap


def today_total() -> Decimal:
    with session_scope() as s:
        return platform_spend.committed_today(s)


def test_platform_paid_calls_are_refused_until_an_allowance_is_set():
    with pytest.raises(BudgetExceeded) as exc:
        spend(guard(A, key_source="platform"))
    assert exc.value.period == "platform"


def test_the_platform_allowance_is_shared_by_every_owner(platform_cap):
    platform_cap(0.03)
    a, b = guard(A, key_source="platform"), guard(B, key_source="platform")
    spend(a), spend(b), spend(a)
    with pytest.raises(BudgetExceeded):
        spend(b)
    assert today_total() == Decimal("0.03")


def test_owners_own_keys_never_draw_on_the_platform_allowance(platform_cap):
    platform_cap(0.01)
    spend(guard(A, key_source="platform"))
    for _ in range(3):
        spend(guard(B))  # B pays with its own key
    assert today_total() == CENT


def test_settling_and_failing_move_the_platform_total(platform_cap):
    platform_cap(1)
    g = guard(A, cap=1, key_source="platform")
    entry = spend(g, Decimal("0.10"))
    cost = g.settle(entry, model="claude-haiku-5-5", usage=UsageCounts(input_tokens=1000), request_id=None)
    assert today_total() == cost < Decimal("0.10")
    failed = spend(g, Decimal("0.20"))
    g.fail(failed, "timeout")
    assert today_total() == cost


def test_the_platform_allowance_cannot_be_overshot_by_owners_racing(platform_cap):
    platform_cap(0.10)
    results: list[bool] = []
    lock = threading.Lock()

    def try_spend(scope: InstanceScope) -> None:
        try:
            spend(guard(scope, cap=1, key_source="platform"))
            ok = True
        except BudgetExceeded:
            ok = False
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=try_spend, args=(A if i % 2 else B,)) for i in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 10
    assert today_total() == Decimal("0.10")
    with session_scope() as s:
        [row] = s.scalars(select(PlatformSpend)).all()
        assert row.day == datetime.now(UTC).date()


def test_an_unknown_key_source_is_refused():
    with pytest.raises(ValueError):
        guard(A, key_source="someone-else")
