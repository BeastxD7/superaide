"""Superaide AI credits: the platform's Claude key, only for owners who opted in, within their own
allowance and the platform's daily cap, never exposed (ADR 0009, owner decision update)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from superaide.brain.budget import BudgetExceeded, BudgetGuard, Limits
from superaide.brain.llm import ClaudeLLM, FakeLLM, RoutedLLM
from superaide.config import get_settings
from superaide.db.models import AuditLog, SpendEntry
from superaide.db.session import session_scope
from superaide.factory import brain_status, build_llm, with_credits
from superaide.tenancy.context import InstanceScope
from superaide.vault.store import runtime_settings
from tests.test_loop import owner_client  # noqa: F401  (fixture)

PLATFORM_KEY = "sk-ant-platform-secret-key-000000000000"
A = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
B = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())


@pytest.fixture
def credits(monkeypatch):
    """The server offers credits, in hosted mode: .env keys are never an owner's own."""
    env = get_settings()
    monkeypatch.setattr(env, "deploy_mode", "hosted")
    monkeypatch.setattr(env, "platform_ai_enabled", True)
    monkeypatch.setattr(env, "platform_anthropic_api_key", PLATFORM_KEY)
    monkeypatch.setattr(env, "platform_daily_cap_usd", 5.0)
    monkeypatch.setattr(env, "fake_llm", False)
    return env


def live(settings):
    return settings.model_copy(update={"fake_llm": False})


def test_an_owner_who_did_not_opt_in_never_gets_the_platform_key(owner_client, credits):  # noqa: F811
    settings = live(runtime_settings())
    assert settings.use_platform_ai is False and settings.anthropic_api_key == ""
    assert with_credits(settings).model_chat == settings.model_chat
    assert not brain_status(settings).ready and isinstance(build_llm(settings), FakeLLM)


def test_opting_in_runs_every_unserved_role_on_credits_with_their_own_guard(owner_client, credits):  # noqa: F811
    r = owner_client.post("/app/providers/credits", json={"on": True})
    assert r.status_code == 200 and r.json()["credits"]["opted_in"] is True and PLATFORM_KEY not in r.text
    settings = live(runtime_settings())
    assert settings.use_platform_ai and settings.anthropic_api_key == ""  # the key never enters their settings
    assert PLATFORM_KEY not in settings.model_dump_json()
    effective = with_credits(settings)
    assert {effective.model_chat, effective.model_triage, effective.model_deep} == {"platform::claude-haiku-5-5"}
    assert brain_status(settings).provider == "platform"
    llm = build_llm(settings)
    assert isinstance(llm, RoutedLLM) and isinstance(llm.routes["platform"], ClaudeLLM)
    assert llm.routes["platform"].guard.key_source == "platform"
    with session_scope() as s:
        assert all(PLATFORM_KEY not in str(a.detail) for a in s.scalars(select(AuditLog)))


def test_the_owners_own_key_wins_for_roles_it_can_serve(owner_client, credits):  # noqa: F811
    owner_client.post("/app/providers/credits", json={"on": True})
    own = live(runtime_settings()).model_copy(update={"anthropic_api_key": "sk-ant-owner-own-key"})
    effective = with_credits(own)
    assert effective.model_chat == own.model_chat and "platform" not in effective.model_deep
    assert all("platform" not in m for m in (effective.model_chat, effective.model_triage, effective.model_deep))
    assert isinstance(build_llm(own), ClaudeLLM)  # their own Claude key pays for everything


def test_without_the_switch_credits_are_not_offered_and_hosted_mode_never_lends(owner_client, credits, monkeypatch):  # noqa: F811
    monkeypatch.setattr(credits, "platform_ai_enabled", False)
    monkeypatch.setattr(credits, "anthropic_api_key", PLATFORM_KEY)  # the server has a key in .env
    r = owner_client.post("/app/providers/credits", json={"on": True})
    assert r.status_code == 409
    assert owner_client.get("/app/providers").json()["credits"]["available"] is False
    settings = live(runtime_settings())
    assert settings.anthropic_api_key == "" and not brain_status(settings).ready


def credit_guard(scope: InstanceScope) -> BudgetGuard:
    return BudgetGuard(10, limits=Limits(), scope=scope, key_source="platform")


def spend(g: BudgetGuard, amount: str) -> uuid.UUID:
    return g.reserve(purpose="chat_turn", model="claude-haiku-5-5", worst_case_usd=Decimal(amount))


def test_each_owner_has_their_own_daily_and_monthly_credit_allowance(credits, monkeypatch):
    monkeypatch.setattr(credits, "platform_owner_daily_usd", 0.03)
    monkeypatch.setattr(credits, "platform_owner_monthly_usd", 0.05)
    a, b = credit_guard(A), credit_guard(B)
    spend(a, "0.02")
    with pytest.raises(BudgetExceeded) as day:
        spend(a, "0.02")
    assert day.value.period == "platform_day"
    spend(b, "0.03")  # B's allowance is its own
    with session_scope() as s:
        rows = s.scalars(select(SpendEntry)).all()
        assert {r.key_source for r in rows} == {"platform"} and len(rows) == 2


def test_the_platform_daily_cap_holds_across_owners(credits, monkeypatch):
    monkeypatch.setattr(credits, "platform_daily_cap_usd", 0.04)
    spend(credit_guard(A), "0.03")
    with pytest.raises(BudgetExceeded) as cap:
        spend(credit_guard(B), "0.02")
    assert cap.value.period == "platform"


@pytest.mark.parametrize("period", ["platform_day", "platform", "platform_month"])
def test_running_out_of_credits_gets_an_honest_reply(agent, period):
    with session_scope() as s:
        reply = agent._budget_reply(s, BudgetExceeded("used", period))
    assert "Superaide credits" in reply and "own AI key" in reply


def test_one_owners_opt_in_never_reaches_another(owner_client, credits):  # noqa: F811
    from superaide.tenancy.context import use_instance

    owner_client.post("/app/providers/credits", json={"on": True})
    assert runtime_settings().use_platform_ai is True  # the owner's own instance
    with use_instance(B):  # any other instance
        other = live(runtime_settings())
        assert other.use_platform_ai is False and not brain_status(other).ready
        assert with_credits(other).model_chat == other.model_chat


SERVER_KEY = "sk-ant-server-env-key-111111111111111"


@pytest.mark.parametrize(
    ("listed", "own"), [("S@Example.com , ops@example.com", True), ("s@exampel.com", False), ("", False)]
)
def test_the_operators_own_ava_uses_the_server_key_as_its_own(owner_client, credits, monkeypatch, listed, own):  # noqa: F811
    from decimal import Decimal

    from superaide.brain import platform_spend
    from superaide.tenancy.context import use_instance

    monkeypatch.setattr(credits, "platform_ai_enabled", False)
    monkeypatch.setattr(credits, "anthropic_api_key", SERVER_KEY)
    monkeypatch.setattr(credits, "platform_key_owner_emails", listed)
    monkeypatch.setattr(credits, "platform_daily_cap_usd", 0.0)  # no credits at all: the key is theirs
    monkeypatch.setattr(credits, "platform_owner_daily_usd", 0.01)
    settings = live(runtime_settings())
    assert (settings.anthropic_api_key == SERVER_KEY) is own and brain_status(settings).ready is own
    if own:
        llm = build_llm(settings)
        assert isinstance(llm, ClaudeLLM) and llm.guard.key_source == "owner"
        # More than this owner's credit allowance (set to US$0.01 above), within the normal budget.
        llm.guard.reserve(purpose="chat_turn", model="claude-haiku-5-5", worst_case_usd=Decimal("0.05"))
        with session_scope() as s:
            assert platform_spend.committed_today(s) == 0  # not counted in the platform cap
    assert SERVER_KEY not in owner_client.get("/app/providers").text
    with use_instance(B):  # anyone else's Ava never gets it
        assert runtime_settings().anthropic_api_key == ""
