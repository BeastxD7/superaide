"""A turn whose model phase or reply send raises still keeps the message and answers (#83)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from superaide.brain.llm import FakeLLM
from superaide.pipeline import Agent
from superaide.platform.db.models import Event
from superaide.platform.db.session import session_scope
from tests.conftest import MANAGER_TG, tg


class Overloaded(Exception):
    pass


def boom(*_a, **_k):
    raise Overloaded("529 overloaded")


def events(direction: str) -> list[Event]:
    with session_scope() as s:
        rows = list(s.scalars(select(Event).where(Event.direction == direction)))
        s.expunge_all()
        return rows


def test_a_model_failure_keeps_the_message_and_apologises(manager, outbox, settings):
    agent = Agent(llm=FakeLLM(boom), messengers=outbox, settings=settings)
    result = agent.handle(tg(MANAGER_TG, "what's on today?"))
    assert "try again" in (result.reply or "")
    assert [e.content for e in events("in")] == ["what's on today?"]
    assert any("try again" in e.content for e in events("out"))
    assert any("try again" in m for m in [x[-1] if isinstance(x, tuple) else str(x) for x in outbox["telegram"].sent])


def test_a_send_failure_other_than_delivery_failed_does_not_lose_the_turn(manager, agent, outbox, monkeypatch):
    def rate_limited(*_a, **_k):
        raise RuntimeError("ratelimited")

    monkeypatch.setattr(outbox["telegram"], "send_to_context", rate_limited)
    result = agent.handle(tg(MANAGER_TG, "hello"))
    assert result.mode == "known"
    assert [e.content for e in events("in")] == ["hello"]
    [out] = events("out")
    assert out.meta.get("undelivered") == "RuntimeError"


@pytest.mark.parametrize("text", ["hi"])
def test_the_guard_is_quiet_when_nothing_fails(manager, agent, text):
    assert "try again" not in (agent.handle(tg(MANAGER_TG, text)).reply or "")


def test_a_failing_receptionist_falls_back_to_the_fixed_stranger_reply(agent, monkeypatch):
    import superaide.reception.service as reception

    monkeypatch.setattr(reception, "handle_stranger", boom)
    result = agent.handle(tg("999", "hello"))
    assert result.mode == "stranger" and "AI assistant" in (result.reply or "")
