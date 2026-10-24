"""With a model connected, "remind me…" goes to Ava, who reads typos and phrasing and sets
the reminder with her tool; the code-only reader is only for when no model is connected."""

from __future__ import annotations

from sqlalchemy import select

from superaide.brain.llm import FakeLLM
from superaide.db.models import Intention
from superaide.db.session import session_scope
from superaide.pipeline import Agent
from tests.pipeline.test_loop import Script, owner_client, tool_use  # noqa: F401

__all__ = ["owner_client"]

TYPO = "please remind me thatt i need to deinnk water in 2mins"


def test_ava_sets_the_reminder_herself_when_a_model_is_connected(owner_client, agent, monkeypatch):
    monkeypatch.setattr(Agent, "_brain_ready", lambda self: True)
    script = Script(
        tool_use("create_reminder", {"task": "Drink water", "when": "in 2 minutes"}),
        "Done: I'll remind you to drink water in 2 minutes.",
    )
    agent.llm = FakeLLM(script)
    res = owner_client.post("/app/chat", json={"text": TYPO})
    assert res.json()["reply"] == "Done: I'll remind you to drink water in 2 minutes."
    with session_scope() as s:
        assert s.scalars(select(Intention.text)).all() == ["Drink water"]
    assert script.seen[0][-1]["content"].endswith(TYPO)  # Ava saw the owner's own words


def test_remember_that_with_a_time_also_goes_to_ava(owner_client, agent, monkeypatch):
    monkeypatch.setattr(Agent, "_brain_ready", lambda self: True)
    script = Script(
        tool_use("create_reminder", {"task": "Go to the gym", "when": "tomorrow at 12pm"}),
        "Set for tomorrow at 12:00.",
    )
    agent.llm = FakeLLM(script)
    owner_client.post("/app/chat", json={"text": "remember that i need to go to the gym tomorrow at 12pm"})
    with session_scope() as s:
        assert s.scalars(select(Intention.text)).all() == ["Go to the gym"]


def test_without_a_model_the_code_reader_still_sets_it(owner_client, monkeypatch):
    monkeypatch.setattr(Agent, "_brain_ready", lambda self: False)
    reply = owner_client.post("/app/chat", json={"text": "remind me to call Priya in 2 hours"}).json()["reply"]
    assert reply.startswith("Got it. I'll remind you")
    with session_scope() as s:
        assert len(s.scalars(select(Intention)).all()) == 1
