"""The provider-neutral contract: the shared spend path, the scripted provider, and taint."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from superaide.brain.llm import SystemBlock as LegacySystemBlock
from superaide.brain.models import SpendEntry
from superaide.brain.providers.base import Priced, metered
from superaide.brain.providers.fake import FakeProvider
from superaide.brain.providers.types import (
    HostedToolResult,
    Message,
    PartDone,
    SourceDoc,
    SystemBlock,
    Text,
    TextDelta,
    ToolCall,
    ToolCallReady,
    ToolResult,
    source_kind,
    sources_in,
    tainted,
)
from superaide.brain.spend.budget import BudgetExceeded, BudgetGuard
from superaide.brain.spend.pricing import UsageCounts
from superaide.platform.db.session import session_scope


def test_the_legacy_system_block_is_the_neutral_one():
    assert LegacySystemBlock is SystemBlock


def test_metered_reserves_then_settles_at_the_real_price():
    guard = BudgetGuard(10)
    response, usage, cost = metered(
        guard, purpose="test", model="claude-sonnet-5-5", worst_case_usd=Decimal("0.01"),
        call=lambda: "ok", priced=lambda r: Priced(UsageCounts(input_tokens=1000, output_tokens=100), "req"),
    )  # fmt: skip
    assert response == "ok" and cost == Decimal("0.003") and usage.input_tokens == 1000
    summary = guard.summary()
    assert summary.settled_usd == Decimal("0.003") and summary.reserved_usd == 0


def test_metered_marks_a_failed_call_and_bills_nothing():
    guard = BudgetGuard(10)

    def boom() -> str:
        raise RuntimeError("network down")

    with pytest.raises(RuntimeError):
        metered(guard, purpose="t", model="claude-haiku-5-5", worst_case_usd=Decimal("0.01"), call=boom,
                priced=lambda r: Priced(UsageCounts()))  # fmt: skip
    with session_scope() as s:
        entry = s.scalar(select(SpendEntry))
        assert entry is not None and entry.status == "failed" and entry.cost_usd == 0


def test_metered_never_calls_past_the_cap():
    called = []
    with pytest.raises(BudgetExceeded):
        metered(BudgetGuard(0.001), purpose="t", model="claude-haiku-5-5", worst_case_usd=Decimal("0.01"),
                call=lambda: called.append(1), priced=lambda r: Priced(UsageCounts()))  # fmt: skip
    assert called == []


class Answer(BaseModel):
    facts: list[str]


def test_the_fake_provider_scripts_steps_streams_and_structured_output():
    script = iter([[Text("Looking."), ToolCall("t1", "recall", {"query": "x"})], "Done."])
    fake = FakeProvider(lambda purpose, system, messages, output_model: next(script))
    events: list = []
    first = fake.step(purpose="chat_turn", model="m", system=[], messages=[Message("user", [Text("hi")])],
                      tools=[], max_tokens=10, on_event=events.append)  # fmt: skip
    assert first.stop == "tool_calls" and isinstance(first.parts[1], ToolCall)
    assert isinstance(events[0], TextDelta) and any(isinstance(e, ToolCallReady) for e in events)
    assert sum(isinstance(e, PartDone) for e in events) == 2
    second = fake.step(purpose="chat_turn", model="m", system=[], messages=[], tools=[], max_tokens=10)
    assert second.stop == "end" and second.parts == [Text("Done.")]

    structured = FakeProvider(lambda *a: {"facts": ["a"]})
    done = structured.complete(purpose="p", model="m", system=[], messages=[], max_tokens=10, output_model=Answer)
    assert done.parsed == Answer(facts=["a"]) and done.text == '{"facts":["a"]}'


def test_web_sources_taint_the_turn_however_they_arrive():
    note = SourceDoc("ava://memory/1", "Note", "x", source_kind("ava://memory/1"))
    page = SourceDoc("https://example.com", "Page", "y", source_kind("https://example.com"))
    assert (note.kind, page.kind, source_kind("ava://cal/9"), source_kind("ava://msg/web/2")) == (
        "memory", "web", "calendar", "message",
    )  # fmt: skip
    assert not tainted([ToolResult("t1", [note]), Text("hi")])
    assert tainted([ToolResult("t1", [note, page])])
    assert tainted([HostedToolResult("s1", "web_search")])  # a hosted web search, even with no results
    assert sources_in(
        [ToolResult("t1", [note]), ToolResult("t2", "plain"), HostedToolResult("s", "web_search", [page])]
    ) == [note, page]
