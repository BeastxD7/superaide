from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from sqlalchemy import select, update

from superaide.brain.budget import AgentFrozen, BudgetExceeded, BudgetGuard, Limits
from superaide.brain.llm import ClaudeLLM, SystemBlock
from superaide.brain.pricing import UnknownModelPrice, UsageCounts, cost_usd
from superaide.db.models import AuditLog, SpendEntry
from superaide.db.session import session_scope
from superaide.policy.killswitch import set_frozen


def test_prices_match_the_published_table():
    assert cost_usd("claude-sonnet-5-5", UsageCounts(input_tokens=1_000_000)) == Decimal("2")
    assert cost_usd("claude-sonnet-5-5", UsageCounts(output_tokens=1_000_000)) == Decimal("10")
    assert cost_usd("claude-opus-5-5", UsageCounts(input_tokens=1_000_000, output_tokens=1_000_000)) == Decimal("24")
    # Cache reads are 0.05x on Sonnet 5.5; 5-minute writes are 1.25x.
    assert cost_usd("claude-sonnet-5-5", UsageCounts(cache_read_tokens=1_000_000)) == Decimal("0.1")
    assert cost_usd("claude-sonnet-5-5", UsageCounts(cache_write_5m_tokens=1_000_000)) == Decimal("2.5")
    # Haiku 5.5 is priced higher once the prompt passes 100K tokens.
    assert cost_usd("claude-haiku-5-5", UsageCounts(input_tokens=100_000)) == Decimal("0.01")
    assert cost_usd("claude-haiku-5-5", UsageCounts(input_tokens=200_000)) == Decimal("0.1")
    # Batch is half price; web search is a cent each.
    assert cost_usd("claude-opus-5-5", UsageCounts(input_tokens=1_000_000), batch=True) == Decimal("2")
    assert cost_usd("claude-sonnet-5-5", UsageCounts(web_search_requests=3)) == Decimal("0.03")


def test_unknown_model_is_refused():
    with pytest.raises(UnknownModelPrice):
        cost_usd("claude-made-up", UsageCounts(input_tokens=1))


class FakeClient:
    """Stands in for anthropic.Anthropic; returns fixed usage."""

    def __init__(self, input_tokens: int = 1000, output_tokens: int = 100, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail
        usage = SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            cache_creation=None,
            server_tool_use=None,
        )
        response = SimpleNamespace(
            usage=usage, content=[SimpleNamespace(type="text", text="hello")], _request_id="req_1"
        )
        self.messages = SimpleNamespace(create=self._create, parse=self._create)
        self._response = response

    def _create(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("network down")
        return self._response


def _call(llm: ClaudeLLM, max_tokens: int = 100):
    return llm.complete(
        purpose="test",
        model="claude-sonnet-5-5",
        system=[SystemBlock("be brief", cache=True)],
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=max_tokens,
    )


def test_calls_are_settled_at_the_real_price():
    guard = BudgetGuard(10)
    llm = ClaudeLLM(guard, client=FakeClient(input_tokens=1000, output_tokens=100))
    result = _call(llm)
    # 1000 * $2/M + 100 * $10/M = $0.003
    assert result.cost_usd == Decimal("0.003")
    summary = guard.summary()
    assert summary.settled_usd == Decimal("0.003") and summary.calls == 1 and summary.reserved_usd == 0


def test_the_cap_refuses_a_call_before_it_is_made():
    guard = BudgetGuard(0.001)  # one tenth of a cent
    client = FakeClient()
    llm = ClaudeLLM(guard, client=client)
    with pytest.raises(BudgetExceeded):
        _call(llm, max_tokens=1000)  # worst case 1000 output tokens = $0.01 > cap
    assert client.calls == 0
    with session_scope() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == "llm_call_refused")) is not None


def test_spend_never_passes_the_cap_over_many_calls():
    guard = BudgetGuard(0.05)
    llm = ClaudeLLM(guard, client=FakeClient(input_tokens=1000, output_tokens=100))
    made = 0
    for _ in range(100):
        try:
            _call(llm)
            made += 1
        except BudgetExceeded:
            break
    summary = guard.summary()
    assert made > 0
    assert summary.settled_usd <= Decimal("0.05")


def test_failed_calls_cost_nothing():
    guard = BudgetGuard(10)
    llm = ClaudeLLM(guard, client=FakeClient(fail=True))
    with pytest.raises(RuntimeError):
        _call(llm)
    with session_scope() as s:
        entry = s.scalar(select(SpendEntry))
        assert entry is not None and entry.status == "failed" and entry.cost_usd == 0
    assert guard.summary().reserved_usd == 0


def test_kill_switch_blocks_model_calls():
    with session_scope() as s:
        set_frozen(s, True, actor="test")
    client = FakeClient()
    with pytest.raises(AgentFrozen):
        _call(ClaudeLLM(BudgetGuard(10), client=client))
    assert client.calls == 0


class StructuredClient:
    """Returns one scripted structured answer and remembers the request."""

    def __init__(self, text: str, stop_reason: str = "end_turn", output_tokens: int = 500) -> None:
        self.kwargs: dict = {}
        self._response = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=1000,
                output_tokens=output_tokens,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
                cache_creation=None,
                server_tool_use=None,
            ),
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
            stop_reason=stop_reason,
            _request_id="req_2",
        )
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return self._response


class Answer(BaseModel):
    facts: list[str]


def _structured(llm: ClaudeLLM):
    return llm.complete(
        purpose="test",
        model="claude-haiku-5-5",
        system=[SystemBlock("extract")],
        messages=[{"role": "user", "content": "a resume"}],
        max_tokens=1500,
        output_model=Answer,
        effort="low",
    )


def test_structured_output_is_validated_and_the_schema_and_effort_are_sent():
    client = StructuredClient('{"facts": ["Leads applied AI"]}')
    result = _structured(ClaudeLLM(BudgetGuard(10), client=client))
    assert result.parsed == Answer(facts=["Leads applied AI"]) and result.stop_reason == "end_turn"
    config = client.kwargs["output_config"]
    assert config["effort"] == "low" and config["format"]["type"] == "json_schema"
    assert "facts" in config["format"]["schema"]["properties"]


def test_an_answer_cut_off_at_max_tokens_is_not_parsed_but_is_still_billed():
    # What happened with a real resume: thinking used most of max_tokens and the JSON stopped mid-string.
    guard = BudgetGuard(10)
    client = StructuredClient('{"facts": ["Leads applied AI", "Wor', stop_reason="max_tokens", output_tokens=1500)
    result = _structured(ClaudeLLM(guard, client=client))
    assert result.parsed is None and result.stop_reason == "max_tokens"
    # 1000 * $0.10/M + 1500 * $0.50/M = $0.00085, counted against the cap.
    assert result.cost_usd == Decimal("0.00085") and guard.summary().settled_usd == Decimal("0.00085")
    with session_scope() as s:
        entry = s.scalar(select(SpendEntry))
        assert entry is not None and entry.status == "settled" and entry.output_tokens == 1500


def test_an_answer_that_does_not_match_the_schema_is_not_parsed_but_is_still_billed():
    guard = BudgetGuard(10)
    result = _structured(ClaudeLLM(guard, client=StructuredClient('{"something": "else"}')))
    assert result.parsed is None and guard.summary().settled_usd > 0


def test_extraction_leaves_room_for_thinking(agent, monkeypatch):
    from superaide.knowledge import facts

    monkeypatch.setattr("superaide.factory.brain_ready", lambda settings: True)
    seen: dict = {}

    def complete(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(parsed=facts.FactList(facts=[]))

    monkeypatch.setattr(agent.llm, "complete", complete)
    assert facts.extract_facts(agent, owner_name="Jordan", text="a resume", source="cv.pdf") == []
    assert seen["max_tokens"] >= 4000 and seen["effort"] == "low"


# --- rolling limits inside the lifetime cap -------------------------------------------


def _reserve(guard, purpose="chat_turn", usd="0.10"):
    return guard.reserve(purpose=purpose, model="claude-haiku-5-5", worst_case_usd=Decimal(usd))


def _age(days: float) -> None:
    """Move every ledger entry back in time, as settled spend (an old unsettled reservation is an orphan
    that the next reserve() releases)."""
    with session_scope() as s:
        s.execute(
            update(SpendEntry).values(
                created_at=datetime.now(UTC) - timedelta(days=days), status="settled", cost_usd=SpendEntry.reserved_usd
            )
        )


def test_the_daily_limit_refuses_a_call_that_would_pass_it_and_names_the_period():
    guard = BudgetGuard(10, limits=Limits(daily_usd=Decimal("0.25")))
    _reserve(guard, usd="0.10")
    _reserve(guard, usd="0.10")
    with pytest.raises(BudgetExceeded) as exc:
        _reserve(guard, usd="0.10")
    assert exc.value.period == "day"
    with session_scope() as s:
        refusal = s.scalar(select(AuditLog).where(AuditLog.action == "llm_call_refused"))
        assert refusal is not None and refusal.detail["reason"] == "day"


def test_spend_older_than_the_window_stops_counting():
    guard = BudgetGuard(10, limits=Limits(daily_usd=Decimal("0.25"), monthly_usd=Decimal("0.35")))
    _reserve(guard, usd="0.20")
    _age(days=2)  # yesterday's spend no longer counts for today...
    _reserve(guard, usd="0.10")
    with pytest.raises(BudgetExceeded) as exc:  # ...but still counts for the month
        _reserve(guard, usd="0.10")
    assert exc.value.period == "month"
    _age(days=31)
    _reserve(guard, usd="0.20")  # a new month


def test_each_bucket_has_its_own_daily_limit():
    guard = BudgetGuard(10, limits=Limits(daily_by_bucket={"research": Decimal("0.05"), "chat": Decimal("1")}))
    _reserve(guard, purpose="web_research", usd="0.04")
    with pytest.raises(BudgetExceeded) as exc:
        _reserve(guard, purpose="web_research", usd="0.04")
    assert exc.value.period == "bucket"
    _reserve(guard, purpose="chat_turn", usd="0.50")  # research being spent doesn't stop chat


def test_the_lifetime_cap_still_comes_first():
    guard = BudgetGuard(0.15, limits=Limits(daily_usd=Decimal("5")))
    _reserve(guard, usd="0.10")
    with pytest.raises(BudgetExceeded) as exc:
        _reserve(guard, usd="0.10")
    assert exc.value.period == "lifetime"


def test_limits_come_from_settings_and_zero_means_no_limit(settings, monkeypatch):
    monkeypatch.setattr(settings, "budget_daily_usd", 0)
    monkeypatch.setattr(settings, "budget_monthly_usd", 3)
    monkeypatch.setattr(settings, "budget_daily_by_bucket", {"chat": 0.4, "memory": 0})
    limits = Limits.from_settings(settings)
    assert limits.daily_usd is None and limits.monthly_usd == Decimal("3")
    assert limits.daily_by_bucket == {"chat": Decimal("0.4")}


def test_ava_says_which_limit_she_hit(agent, manager, monkeypatch):
    from tests.conftest import MANAGER_TG, tg

    def over_today(**_):
        raise BudgetExceeded("today's limit", "day")

    monkeypatch.setattr(agent.llm, "complete", over_today)
    result = agent.handle(tg(MANAGER_TG, "what's on today?"))
    assert "today's thinking budget" in (result.reply or "") and "budget" in result.notes
