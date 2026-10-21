from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from superaide.brain.budget import AgentFrozen, BudgetExceeded, BudgetGuard
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
