"""Superaide AI credits: the platform's Claude key, only for owners who opted in, within their own
allowance and the platform's daily cap, never exposed (ADR 0009, owner decision update)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from superaide.brain.llm import ClaudeLLM, FakeLLM, RoutedLLM
from superaide.brain.spend.budget import BudgetExceeded, BudgetGuard, Limits
from superaide.brain.wiring import brain_status, build_llm, with_credits
from superaide.platform.config import get_settings
from superaide.platform.db.models import AuditLog, SpendEntry
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import InstanceScope
from superaide.vault.store import runtime_settings
from tests.pipeline.test_loop import owner_client  # noqa: F401  (fixture)

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


def test_the_shared_cap_is_not_blamed_on_the_owners_own_credits(agent):
    with session_scope() as s:
        own = agent._budget_reply(s, BudgetExceeded("used", "platform_day"))
        shared = agent._budget_reply(s, BudgetExceeded("used", "platform"))
    assert shared != own and "Everyone" in shared and "shared" in shared
    assert "Superaide credits. Add your own AI key on the AI model page" in shared


@pytest.mark.parametrize("period", ["platform_day", "platform", "platform_month", "day", "bucket", "month", "lifetime"])
def test_a_contact_is_never_given_the_owners_budget_message(agent, period):
    with session_scope() as s:
        reply = agent._budget_reply(s, BudgetExceeded("used", period), owner=False)
    assert "can't help with this right now" in reply
    for leak in ("credits", "AI key", "AI model", "budget", "cap", "US$", "month", "today"):
        assert leak not in reply


@pytest.mark.parametrize("period", ["platform_day", "platform", "platform_month", "month"])
def test_a_known_contact_whose_reply_hits_a_limit_gets_a_neutral_line(model, outbox, settings, colleague, period):
    from superaide.brain.llm import FakeLLM
    from superaide.pipeline import Agent
    from tests.conftest import sl

    def refuse(purpose, system, messages, output_model):
        raise BudgetExceeded("used", period)

    ava = Agent(llm=FakeLLM(refuse), messengers=outbox, settings=settings)
    result = ava.handle(sl("U_PRIYA", "Is he free on Friday?"))
    sent = " ".join(m["text"] for m in outbox["slack"].sent)
    assert "can't help with this right now" in sent
    assert not any(w in sent for w in ("credits", "AI key", "AI model page", "budget"))
    assert result.mode == "known"


def test_the_owner_still_hears_which_limit_was_reached(model, outbox, settings, manager):
    from superaide.brain.llm import FakeLLM
    from superaide.pipeline import Agent
    from tests.conftest import MANAGER_TG, tg

    def refuse(purpose, system, messages, output_model):
        raise BudgetExceeded("used", "platform_day")

    ava = Agent(llm=FakeLLM(refuse), messengers=outbox, settings=settings)
    ava.handle(tg(MANAGER_TG, "hello"))
    assert any("Add your own AI key on the AI model page" in m["text"] for m in outbox["telegram"].sent)


def test_one_owners_opt_in_never_reaches_another(owner_client, credits):  # noqa: F811
    from superaide.platform.tenancy.context import use_instance

    owner_client.post("/app/providers/credits", json={"on": True})
    assert runtime_settings().use_platform_ai is True  # the owner's own instance
    with use_instance(B):  # any other instance
        other = live(runtime_settings())
        assert other.use_platform_ai is False and not brain_status(other).ready
        assert with_credits(other).model_chat == other.model_chat


SERVER_KEY = "sk-ant-server-env-key-111111111111111"


@pytest.mark.parametrize(
    ("listed", "own"),
    [
        ("S@Example.com , ops@example.com", True),
        ("s@exampel.com", False),
        ("", False),
        # One bad entry must not break every agent build (#92): it is skipped, the good one still counts.
        ("s@example.com, you@localhost", True),
        ("s@example.com; you@localhost", True),
        ("you@localhost, not an address", False),
    ],
)
def test_the_operators_own_ava_uses_the_server_key_as_its_own(owner_client, credits, monkeypatch, listed, own):  # noqa: F811
    from decimal import Decimal

    from superaide.brain.spend import platform_spend
    from superaide.platform.tenancy.context import use_instance

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


# --- credits are only offered when they can pay for a call (#64), and the card says so honestly (#65) ---


def test_credits_are_not_offered_while_the_platform_cap_is_zero(owner_client, credits, monkeypatch, caplog):  # noqa: F811
    from superaide.brain import platform_ai

    monkeypatch.setattr(credits, "platform_daily_cap_usd", 0.0)
    assert platform_ai.available() is False
    assert owner_client.get("/app/providers").json()["credits"]["available"] is False
    refused = owner_client.post("/app/providers/credits", json={"on": True})
    assert refused.status_code == 409
    settings = live(runtime_settings())
    assert settings.use_platform_ai is False and not brain_status(settings).ready
    with caplog.at_level("WARNING"):
        platform_ai.warn_if_unusable()
    assert "PLATFORM_DAILY_CAP_USD is 0" in caplog.text
    monkeypatch.setattr(credits, "platform_daily_cap_usd", 1.0)
    assert platform_ai.available() is True
    assert owner_client.post("/app/providers/credits", json={"on": True}).status_code == 200


def test_no_startup_warning_when_credits_are_off_or_the_cap_is_set(credits, monkeypatch, caplog):
    from superaide.brain import platform_ai

    with caplog.at_level("WARNING"):
        platform_ai.warn_if_unusable()  # cap 5: fine
        monkeypatch.setattr(credits, "platform_ai_enabled", False)
        monkeypatch.setattr(credits, "platform_daily_cap_usd", 0.0)
        platform_ai.warn_if_unusable()  # switched off: nothing to say
    assert "PLATFORM_DAILY_CAP_USD" not in caplog.text


@pytest.mark.parametrize(
    ("daily", "monthly", "expected"),
    [(0.5, 5.0, (0.5, 5.0)), (0.0, 5.0, (None, 5.0)), (0.5, 0.0, (0.5, None)), (0.0, 0.0, (None, None))],
)
def test_no_limit_is_sent_as_null_never_as_zero(owner_client, credits, monkeypatch, daily, monthly, expected):  # noqa: F811
    monkeypatch.setattr(credits, "platform_owner_daily_usd", daily)
    monkeypatch.setattr(credits, "platform_owner_monthly_usd", monthly)
    allowance = owner_client.get("/app/providers").json()["credits"]["allowance"]
    assert (allowance["daily_usd"], allowance["monthly_usd"]) == expected


# --- Superaide's credits are priced by Superaide, never by the owner (#59) ---------------------------


def test_an_owner_price_of_zero_cannot_make_credit_paid_calls_free(owner_client, credits):  # noqa: F811
    from superaide.brain.spend.pricing import PriceRegistry, worst_case_cost

    owner_client.post("/app/providers/credits", json={"on": True})
    priced = owner_client.post(
        "/app/providers/prices",
        json={"provider": "anthropic", "model": "claude-haiku-5-5", "input_usd_per_mtok": 0, "output_usd_per_mtok": 0},
    )
    assert priced.status_code == 200  # the owner's own table, for their own key
    settings = live(runtime_settings())
    llm = build_llm(settings)
    assert isinstance(llm, RoutedLLM)
    guard = llm.routes["platform"].guard
    assert guard.key_source == "platform"
    price = guard.prices.price_for("claude-haiku-5-5", "anthropic")
    assert (price.input, price.output) == (Decimal("0.10"), Decimal("0.50"))  # Superaide's price
    assert worst_case_cost("claude-haiku-5-5", 1000, 4000, prices=guard.prices) > 0
    # Even a guard handed the owner's table uses the built-in prices when credits pay.
    free = PriceRegistry(owner={("anthropic", "claude-haiku-5-5"): price.__class__(Decimal(0), Decimal(0))})
    assert BudgetGuard(10, prices=free, key_source="platform").prices.price_for("claude-haiku-5-5").input > 0
    assert BudgetGuard(10, prices=free, key_source="owner").prices.price_for("claude-haiku-5-5").input == 0


# --- who pays decides which limits count (#60, #61) -------------------------------------------------


def owner_guard(scope: InstanceScope, **limits) -> BudgetGuard:
    return BudgetGuard(10, limits=Limits(**limits), scope=scope, key_source="owner")


def test_credit_spend_never_uses_up_the_owners_own_key_limits(credits, monkeypatch):
    """Using the month's credits and then adding their own key, as Ava advises, must work."""
    monkeypatch.setattr(credits, "platform_owner_daily_usd", 0.0)  # only the monthly US$5 allowance
    monkeypatch.setattr(credits, "platform_owner_monthly_usd", 5.0)
    monthly = Limits(daily_usd=Decimal("0.50"), monthly_usd=Decimal("5"), daily_by_bucket={"chat": Decimal("0.40")})
    spend(credit_guard(A), "4.90")  # nearly all of a US$5 month, paid by credits
    own = BudgetGuard(10, limits=monthly, scope=A, key_source="owner")
    spend(own, "0.30")  # the owner's own key still answers: none of the credits count against its limits
    with session_scope() as s:
        assert {r.key_source for r in s.scalars(select(SpendEntry))} == {"platform", "owner"}


def test_the_owners_own_limits_still_hold_for_their_own_key(credits):
    own = owner_guard(A, monthly_usd=Decimal("0.05"))
    spend(own, "0.04")
    with pytest.raises(BudgetExceeded) as month:
        spend(own, "0.04")
    assert month.value.period == "month"
    bucketed = owner_guard(B, daily_by_bucket={"chat": Decimal("0.02")})
    with pytest.raises(BudgetExceeded) as bucket:
        spend(bucketed, "0.03")
    assert bucket.value.period == "bucket"


def test_the_card_allowance_is_the_credits_real_limit(credits, monkeypatch):
    """The owner's chat bucket (US$0.40) and daily budget (US$0.50) don't stop credits short of the allowance."""
    monkeypatch.setattr(credits, "platform_owner_daily_usd", 0.50)
    monkeypatch.setattr(credits, "platform_owner_monthly_usd", 5.0)
    g = BudgetGuard(
        10,
        limits=Limits(daily_usd=Decimal("0.40"), daily_by_bucket={"chat": Decimal("0.40")}),
        scope=A,
        key_source="platform",
    )
    spend(g, "0.45")  # past the owner's chat bucket and daily limit, within the credits allowance
    spend(g, "0.05")  # exactly the allowance
    with pytest.raises(BudgetExceeded) as used_up:
        spend(g, "0.01")
    assert used_up.value.period == "platform_day"  # the credits message, not the generic one


def test_the_lifetime_cap_still_counts_every_call_whoever_paid(credits):
    spend(BudgetGuard(0.05, scope=A, key_source="platform"), "0.04")
    with pytest.raises(BudgetExceeded) as cap:
        spend(BudgetGuard(0.05, scope=A, key_source="owner"), "0.02")
    assert cap.value.period == "lifetime"
    with pytest.raises(BudgetExceeded) as cap2:
        spend(BudgetGuard(0.05, scope=A, key_source="platform"), "0.02")
    assert cap2.value.period == "lifetime"
