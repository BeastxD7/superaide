"""Per-stage timing of a message turn (#412): exact on a hand-driven clock, numbers only, cheap, never in the way."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import String, Text, select

from superaide.brain.llm import FakeLLM
from superaide.main import create_app
from superaide.observability import store, timing
from superaide.observability.models import TurnTiming
from superaide.pipeline import Agent
from superaide.platform.db.session import session_scope
from tests.conftest import MANAGER_TG, tg

ADMIN = {"Authorization": "Bearer test-admin-token"}


class Clock:
    """A clock that moves only when told to."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def tick(self, ms: float) -> None:
        self.now += ms / 1000


def rows() -> list[TurnTiming]:
    with session_scope() as s:
        found = list(s.scalars(select(TurnTiming).order_by(TurnTiming.created_at)))
        s.expunge_all()
        return found


# --- the timer ----------------------------------------------------------------------------------------------------


def test_each_stage_is_timed_exactly_and_nested_time_is_not_counted_twice():
    clock = Clock()
    with timing.open_turn(clock) as timer:
        with timing.span("identity"):
            clock.tick(7)
        with timing.span("model"):
            clock.tick(100)
            with timing.span("airlock"):  # inside the model call: its own time, taken out of the model's
                clock.tick(5)
            clock.tick(20)
            timing.first_token()
        with timing.span("reply"):
            clock.tick(3)
            with timing.span("send"):
                clock.tick(40)
        with timing.span("model"):  # a second call adds to the first
            clock.tick(10)
    assert timer.stage_ms() == {"identity": 7, "model": 130, "airlock": 5, "reply": 3, "send": 40}
    assert timer.model_calls == 2
    assert timer.first_token_ms() == 132
    assert timer.total_ms() == 185
    assert sum(timer.stage_ms().values()) == timer.total_ms()  # exclusive times add up to the turn


def test_a_stage_that_raised_is_still_timed_and_the_timer_is_closed():
    clock = Clock()
    with pytest.raises(RuntimeError), timing.open_turn(clock) as timer:
        with timing.span("model"):
            clock.tick(12)
            raise RuntimeError("overloaded")
    assert timer.stage_ms() == {"model": 12}
    assert timing.current() is None  # the next turn on this thread starts clean


def test_with_no_timer_open_every_helper_does_nothing():
    assert timing.current() is None
    with timing.span("model"):
        pass
    timing.first_token()
    llm = object()
    assert timing.timed_llm(llm) is llm

    @timing.timed("policy")
    def add(a: int, b: int) -> int:
        return a + b

    assert add(1, 2) == 3


def test_the_timers_cost_is_negligible():
    with timing.open_turn() as timer:
        start = time.perf_counter()
        for _ in range(10_000):
            with timing.span("model"):
                pass
        spent = time.perf_counter() - start
    assert timer.model_calls == 10_000
    assert spent / 10_000 < 0.0001  # well under 0.1 ms per stage; a turn has about ten


# --- the pipeline -------------------------------------------------------------------------------------------------


def test_an_owner_turn_records_every_stage_it_went_through(manager, agent):
    agent.handle(tg(MANAGER_TG, "what's on today?"))
    [row] = rows()
    assert (row.provider, row.mode) == ("telegram", "known")
    for stage in ("verify", "identity", "gate", "retrieval", "model", "reply", "send"):
        assert getattr(row, f"{stage}_ms") is not None, stage
    assert row.model_calls >= 1
    assert row.total_ms >= row.model_ms


def test_a_stranger_turn_has_no_model_stage(agent):
    agent.handle(tg("999000", "hello, is this Jordan?"))
    [row] = rows()
    assert row.mode == "stranger"
    assert row.model_ms is None and row.retrieval_ms is None  # not reached, so NULL and not 0
    assert row.model_calls == 0
    assert row.identity_ms is not None and row.send_ms is not None


def test_the_pipeline_uses_the_clock_it_is_given(manager, agent, monkeypatch):
    ticks = iter(range(10_000))
    monkeypatch.setattr(timing, "CLOCK", lambda: next(ticks) / 100)  # every reading is 10 ms after the last
    agent.handle(tg(MANAGER_TG, "hi"))
    [row] = rows()
    for stage in ("verify", "identity", "gate", "model", "send"):
        assert getattr(row, f"{stage}_ms") % 10 == 0 and getattr(row, f"{stage}_ms") >= 10, stage


def test_a_message_dropped_for_a_bad_signature_is_timed_too(agent):
    msg = tg(MANAGER_TG, "hi")
    msg.verified = False
    assert agent.handle(msg).mode == "dropped"
    [row] = rows()
    assert row.mode == "dropped" and row.verify_ms is not None and row.identity_ms is None


def test_nothing_is_timed_or_stored_when_the_flag_is_off(manager, outbox, settings):
    off = Agent(
        llm=FakeLLM(lambda *a: None),
        messengers=outbox,
        settings=settings.model_copy(update={"pipeline_timing_enabled": False}),
    )
    off.handle(tg(MANAGER_TG, "hi"))
    assert rows() == []


def test_a_failure_to_store_the_timing_never_breaks_the_reply(manager, agent, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("database gone")

    monkeypatch.setattr(store, "TurnTiming", broken)
    result = agent.handle(tg(MANAGER_TG, "hi"))
    assert result.mode == "known" and result.reply


def test_one_structured_line_of_numbers_is_logged_per_turn(manager, agent, caplog):
    caplog.set_level("INFO", logger="superaide.timing")
    agent.handle(tg(MANAGER_TG, "SECRET-CANARY-7731 what's on today?"))
    [line] = [r.getMessage() for r in caplog.records if r.name == "superaide.timing"]
    assert line.startswith("turn_timing provider=telegram mode=known total_ms=")
    assert "model_ms=" in line and "model_calls=" in line
    assert "CANARY" not in line and "today" not in line


# --- privacy: numbers and two short words only --------------------------------------------------------------------


def test_no_message_content_is_in_the_timing_data(manager, agent):
    agent.handle(tg(MANAGER_TG, "SECRET-CANARY-7731 please email priya.tan@example.com"))
    agent.handle(tg("999000", "CANARY-STRANGER-5520 who are you"))
    dumped = json.dumps([{c.name: str(getattr(r, c.name)) for c in TurnTiming.__table__.columns} for r in rows()])
    for canary in ("CANARY", "priya", "example.com", "email"):
        assert canary not in dumped


def test_the_table_has_no_free_text_column():
    texts = [c.name for c in TurnTiming.__table__.columns if isinstance(c.type, Text)]
    strings = {c.name: c.type.length for c in TurnTiming.__table__.columns if isinstance(c.type, String)}
    assert texts == []
    assert set(strings) == {"provider", "mode"} and max(strings.values()) <= 16


# --- the operator route -------------------------------------------------------------------------------------------


def add_turn(provider: str, model_ms: int | None, total_ms: int) -> None:
    with session_scope() as s:
        s.add(TurnTiming(provider=provider, mode="known", total_ms=total_ms, model_ms=model_ms, model_calls=1))


@pytest.fixture
def client(agent):
    return TestClient(create_app(agent))


def test_latency_gives_p50_and_p95_per_stage_and_per_channel(client):
    for ms in (100, 200, 300, 400, 500):
        add_turn("telegram", ms, ms + 50)
    add_turn("slack", 1000, 1100)
    add_turn("slack", None, 20)  # a turn that never reached the model
    body = client.get("/api/latency", headers=ADMIN).json()
    data = body.get("data", body)
    assert data["window_hours"] == 24 and data["stage_order"][0] == "verify"
    assert data["overall"]["turns"] == 7
    telegram = next(c for c in data["channels"] if c["provider"] == "telegram")
    assert telegram["turns"] == 5
    assert telegram["stages"]["model"] == {"n": 5, "p50": 300.0, "p95": 480.0}
    assert telegram["stages"]["total"]["p50"] == 350.0
    slack = next(c for c in data["channels"] if c["provider"] == "slack")
    assert slack["stages"]["model"]["n"] == 1  # the NULL is not a 0
    assert data["overall"]["stages"]["send"] == {"n": 0, "p50": None, "p95": None}


def test_latency_only_looks_at_the_window(client):
    add_turn("web", 10, 11)
    with session_scope() as s:
        old = TurnTiming(provider="web", mode="known", total_ms=9999, model_calls=0)
        s.add(old)
        s.flush()
        s.execute(TurnTiming.__table__.update().where(TurnTiming.id == old.id).values(created_at="2020-01-01"))
    data = client.get("/api/latency?hours=48", headers=ADMIN).json()
    data = data.get("data", data)
    assert data["overall"]["turns"] == 1 and data["window_hours"] == 48


@pytest.mark.parametrize("hours", ["0", "721", "abc", "-5"])
def test_latency_refuses_a_bad_window(client, hours):
    assert client.get(f"/api/latency?hours={hours}", headers=ADMIN).status_code == 422


def test_latency_needs_the_operator_token_and_leaks_no_text(client):
    assert client.get("/api/latency").status_code in (401, 403)
    assert client.get("/api/latency", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_latency_with_nothing_recorded_is_empty_not_an_error(client):
    data = client.get("/api/latency", headers=ADMIN).json()
    data = data.get("data", data)
    assert data["overall"]["turns"] == 0 and data["channels"] == []
