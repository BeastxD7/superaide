"""The latency budget of a default DM turn (#413): exact, on a hand-driven clock that charges 1 ms per database
statement, so a stage that quietly does more work fails CI. Also pins the order a turn must meet its stages in."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from superaide import pipeline
from superaide.observability import budget, timing
from superaide.platform.db.session import session_scope
from tests.conftest import MANAGER_TG, tg
from tests.observability.test_timing import rows


@contextmanager
def statement_clock(monkeypatch):
    """timing.CLOCK that moves 1 ms per database statement and otherwise stands still."""
    now = [100.0]
    monkeypatch.setattr(timing, "CLOCK", lambda: now[0])

    def tick(*_a, **_k):
        now[0] += 0.001

    event.listen(Engine, "before_cursor_execute", tick)
    try:
        yield
    finally:
        event.remove(Engine, "before_cursor_execute", tick)


def turn(agent, monkeypatch, text_="what's on today?", sender=MANAGER_TG):
    seen: list[timing.TurnTimer] = []
    real = timing.open_turn

    @contextmanager
    def spy(clock=None):
        with real(clock) as timer:
            seen.append(timer)
            yield timer

    monkeypatch.setattr(pipeline.timing, "open_turn", spy)
    with statement_clock(monkeypatch):
        agent.handle(tg(sender, text_))
    [row] = rows()
    stages = {s: getattr(row, f"{s}_ms") for s in timing.STAGES if getattr(row, f"{s}_ms") is not None}
    return seen[0], stages, row


def test_a_default_dm_turn_is_inside_its_budget(manager, agent, monkeypatch):
    timer, stages, row = turn(agent, monkeypatch)
    assert budget.overruns(stages, total_ms=row.total_ms, model_calls=row.model_calls) == []
    assert row.model_calls == 1  # heavy extras (second opinion, dissent) are not on the default path


def test_a_stage_that_does_more_work_fails_the_budget(manager, agent, monkeypatch):
    real = pipeline.resolve

    def chatty(s, *a, **k):  # an N+1 in the identity stage
        for _ in range(40):
            s.execute(text("SELECT 1"))
        return real(s, *a, **k)

    monkeypatch.setattr(pipeline, "resolve", chatty)
    _, stages, row = turn(agent, monkeypatch)
    found = budget.overruns(stages, total_ms=row.total_ms, model_calls=row.model_calls)
    assert any(o.what == "stage identity" for o in found), found
    assert any(o.what == "the whole turn" for o in found), found


def test_a_second_model_call_fails_the_budget():
    assert [o.what for o in budget.overruns({}, total_ms=1, model_calls=2)] == ["model calls"]


def test_the_audience_gate_comes_before_retrieval_and_the_model(manager, agent, monkeypatch):
    timer, _, _ = turn(agent, monkeypatch)
    assert budget.order_violations(timer.order) == [], timer.order
    for earlier, later in zip(budget.PRIVACY_ORDER, budget.PRIVACY_ORDER[1:], strict=False):
        assert timer.order.index(earlier) < timer.order.index(later)


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        (["identity", "retrieval", "gate", "model"], ["retrieval started before gate"]),
        (["identity", "gate", "model", "retrieval"], ["model started before retrieval"]),
        (
            ["identity", "retrieval", "model"],
            ["retrieval ran with no audience gate", "the model ran with no audience gate"],
        ),
        (["identity", "gate", "retrieval", "model", "reply"], []),
    ],
)
def test_the_order_check_catches_a_stage_that_moved_ahead_of_the_gate(order, expected):
    assert budget.order_violations(order) == expected


def test_a_stranger_turn_never_reaches_retrieval_or_the_model(agent, monkeypatch):
    timer, stages, row = turn(agent, monkeypatch, "hello, is this Jordan?", sender="999000")
    assert "retrieval" not in timer.order and "model" not in timer.order
    assert row.model_calls == 0
    assert budget.overruns(stages, total_ms=row.total_ms, model_calls=row.model_calls) == []


def test_the_budget_lines_are_the_stages_the_timer_knows():
    assert set(budget.STAGE_BUDGET_MS) == set(timing.STAGES)
    assert set(budget.PRIVACY_ORDER) <= set(timing.STAGES)
    assert sum(budget.STAGE_BUDGET_MS.values()) >= budget.TURN_BUDGET_MS  # no stage line alone is tighter than the turn
    assert session_scope  # the module imports the real session, not a stand-in
