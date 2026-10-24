"""Prices per provider and model: built-in, the owner's own, free only when truly local, and
settlement that never undercounts (ADR 0007, section 7)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from superaide.brain.budget import BudgetGuard
from superaide.brain.pricing import (
    FREE,
    PriceRegistry,
    UnknownModelPrice,
    UsageCounts,
    cost_usd,
    is_local_url,
    owner_prices,
    set_owner_price,
    worst_case_cost,
)
from superaide.platform.db.models import AuditLog, SpendEntry
from superaide.platform.db.session import session_scope


def test_claude_prices_are_built_in_and_anything_else_is_refused():
    assert cost_usd("claude-haiku-5-5", UsageCounts(input_tokens=100_000)) == Decimal("0.01")
    with pytest.raises(UnknownModelPrice):
        cost_usd("gpt-made-up", UsageCounts(input_tokens=1), provider="openai")
    with pytest.raises(UnknownModelPrice):  # a Claude id under another provider isn't priced by accident
        cost_usd("claude-haiku-5-5", UsageCounts(input_tokens=1), provider="openrouter")


def test_an_owner_price_is_stored_audited_and_used_for_reservation_and_settlement():
    with session_scope() as s:
        set_owner_price(
            s, provider="openrouter", model="acme/fast-1", input_usd_per_mtok=Decimal("1"),
            output_usd_per_mtok=Decimal("4"), user_id=None,
        )  # fmt: skip
    with session_scope() as s:
        prices = owner_prices(s)
        assert s.scalar(select(AuditLog).where(AuditLog.action == "model_price_set")) is not None
    usage = UsageCounts(input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost_usd("acme/fast-1", usage, provider="openrouter", prices=prices) == Decimal("5")
    # No explicit cache writes on this model, so the worst case doesn't add a cache multiplier.
    assert worst_case_cost("acme/fast-1", 0, 0, provider="openrouter", prices=prices) == Decimal("0.002")
    guard = BudgetGuard(10, prices=prices)
    entry = guard.reserve(purpose="chat_turn", model="acme/fast-1", worst_case_usd=Decimal("1"), provider="openrouter")
    cost = guard.settle(entry, model="acme/fast-1", usage=usage, request_id=None, provider="openrouter")
    assert cost == Decimal("5")
    with session_scope() as s:
        row = s.scalar(select(SpendEntry))
        assert row is not None and row.provider == "openrouter" and row.status == "settled"


def test_negative_prices_are_refused():
    with session_scope() as s, pytest.raises(ValueError):
        set_owner_price(s, provider="custom", model="m", input_usd_per_mtok=Decimal("-1"),
                        output_usd_per_mtok=Decimal("1"), user_id=uuid.uuid4())  # fmt: skip


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("http://localhost:11434/v1", True),
        ("http://127.0.0.1:8000/v1", True),
        ("http://10.0.0.5:11434/v1", True),
        ("http://192.168.1.20/v1", True),
        ("http://[::1]:11434/v1", True),
        ("http://169.254.169.254/latest", False),  # cloud metadata: link-local, never "local and free"
        ("https://8.8.8.8/v1", False),
        ("https://", False),
        ("", False),
        (None, False),
    ],
)
def test_only_loopback_and_private_addresses_count_as_local(url, local):
    assert is_local_url(url) is local


def test_a_local_server_is_free_only_at_a_local_address():
    registry = PriceRegistry()
    assert registry.price_for("llama3.2", "ollama", base_url="http://localhost:11434/v1") == FREE
    with pytest.raises(UnknownModelPrice):  # an "ollama" preset pointed at a public host is not free
        registry.price_for("llama3.2", "ollama", base_url="https://8.8.8.8/v1")
    with pytest.raises(UnknownModelPrice):  # hosted providers are never free because of their address
        registry.price_for("gpt-x", "openai", base_url="http://localhost:8000/v1")


def test_missing_usage_is_settled_at_the_reservation_never_at_zero():
    guard = BudgetGuard(10)
    entry = guard.reserve(purpose="chat_turn", model="claude-haiku-5-5", worst_case_usd=Decimal("0.02"))
    cost = guard.settle(entry, model="claude-haiku-5-5", usage=UsageCounts(), request_id=None, known=False)
    assert cost == Decimal("0.02") and guard.summary().settled_usd == Decimal("0.02")
    with session_scope() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == "usage_missing")) is not None


def test_a_reported_cost_counts_when_it_is_higher_and_a_gap_is_audited():
    guard = BudgetGuard(10)
    usage = UsageCounts(input_tokens=50_000)  # we compute $0.005
    first = guard.reserve(purpose="chat_turn", model="claude-haiku-5-5", worst_case_usd=Decimal("1"))
    assert guard.settle(first, model="claude-haiku-5-5", usage=usage, request_id=None,
                        reported_cost_usd=Decimal("0.0075")) == Decimal("0.0075")  # fmt: skip
    second = guard.reserve(purpose="chat_turn", model="claude-haiku-5-5", worst_case_usd=Decimal("1"))
    assert guard.settle(second, model="claude-haiku-5-5", usage=usage, request_id=None,
                        reported_cost_usd=Decimal("0.00499")) == Decimal("0.005")  # fmt: skip
    with session_scope() as s:
        gaps = s.scalars(select(AuditLog).where(AuditLog.action == "cost_mismatch")).all()
        assert len(gaps) == 1 and gaps[0].detail["reported_usd"] == 0.0075


def test_an_owner_price_for_a_new_claude_model_still_counts_cache_writes_and_searches():
    with session_scope() as s:
        set_owner_price(s, provider="anthropic", model="claude-new-6", input_usd_per_mtok=Decimal("1"),
                        output_usd_per_mtok=Decimal("5"), user_id=None)  # fmt: skip
        prices = owner_prices(s)
    usage = UsageCounts(cache_write_1h_tokens=1_000_000, web_search_requests=2)
    assert cost_usd("claude-new-6", usage, prices=prices) == Decimal("2.02")  # 2x input, plus $0.01 a search
    assert worst_case_cost("claude-new-6", 0, 0, prices=prices) == Decimal("0.004")  # 2,000 overhead tokens at 2x


def test_built_in_tables_price_openai_xai_and_gemini_and_unknown_models_still_ask():
    from datetime import date

    from superaide.brain.price_tables import GEMINI, Scheduled

    registry = PriceRegistry()
    assert cost_usd("gpt-5.4-mini", UsageCounts(input_tokens=1_000_000, output_tokens=1_000_000), provider="openai",
                    prices=registry) == Decimal("5.25")  # fmt: skip
    # Above 272K input tokens the flagship long-context rates apply.
    assert cost_usd("gpt-6-astra", UsageCounts(input_tokens=300_000), provider="openai") == Decimal("6")
    assert cost_usd("grok-4.7", UsageCounts(input_tokens=200_000), provider="xai") == Decimal("0.8")  # >=200K: $4/M
    assert cost_usd("grok-4.7", UsageCounts(cache_read_tokens=100_000), provider="xai") == Decimal("0.05")
    assert registry.price_for("models/gemini-2.5-flash", "gemini").input == Decimal("0.30")
    argon = GEMINI["gemini-4-argon"]
    assert isinstance(argon, Scheduled)
    assert argon.current(date(2026, 11, 30)).input == Decimal("2.00") and argon.current(
        date(2026, 12, 1)
    ).input == Decimal("4.00")
    for provider, model in [("openai", "gpt-made-up"), ("xai", "grok-9"), ("gemini", "gemini-9"), ("azure", "gpt-5.4")]:
        with pytest.raises(UnknownModelPrice):
            registry.price_for(model, provider)


def test_openrouter_listed_prices_are_read_from_its_models_list():
    from superaide.brain.providers.openai_compat import listed_prices

    models = [
        {
            "id": "acme/fast",
            "pricing": {"prompt": "0.000001", "completion": "0.000004", "input_cache_read": "0.0000001"},
        },
        {"id": "openrouter/auto", "pricing": {"prompt": "-1", "completion": "-1"}},
        {"id": "broken", "pricing": {"prompt": "n/a"}},
    ]
    prices = listed_prices(models)
    assert prices == {"acme/fast": {"input": "1.000000", "output": "4.000000", "cache_read": "0.1000000"}}
