"""ADR 0010 TR5 with ADR 0009: each owner's receptionist is their own Ava, from their own public information."""

from __future__ import annotations

from sqlalchemy import select

from superaide.brain.llm import FakeLLM
from superaide.channels.base import FakeMessenger
from superaide.config import get_settings
from superaide.db.session import session_scope
from superaide.pipeline import Agent
from superaide.reception.models import ReceptionMessage
from superaide.reception.turn import ReceptionTurn, TakenMessage
from superaide.tenancy.context import use_instance
from tests.api.conftest import OTHER, OWNER, hire, signup
from tests.api.test_triage_instances import scope_of
from tests.conftest import sl
from tests.test_reception import Receptionist


def test_two_owners_are_isolated(browser, monkeypatch, settings):
    script, outbox = Receptionist(), {"telegram": FakeMessenger(), "slack": FakeMessenger(), "web": FakeMessenger()}

    monkeypatch.setattr(get_settings(), "allow_many_owners", True)
    import superaide.brain.wiring as wiring

    monkeypatch.setattr(wiring, "brain_ready", lambda s: True)
    a, b = browser(), browser()
    assert signup(a).status_code == 200 and hire(a).status_code == 200
    assert signup(b, **OTHER).status_code == 200 and hire(b).status_code == 200
    (sa, _pa), (sb, _pb) = scope_of(OWNER["email"]), scope_of(OTHER["email"])
    assert a.put("/app/receptionist", json={"public_profile": "A-PROFILE bakes bread"}).status_code == 200
    assert b.put("/app/receptionist", json={"public_profile": "B-PROFILE builds boats", "show_first_name": True}
                 ).status_code == 200  # fmt: skip
    script.out = ReceptionTurn(reply="Hello!", take_message=TakenMessage(message="hi there"))
    agent = Agent(llm=FakeLLM(script), messengers=outbox, settings=settings)
    with use_instance(sb):
        rb = agent.handle(sl("U_VISITOR_1", "hello"))
    with use_instance(sa):
        ra = agent.handle(sl("U_VISITOR_2", "hello"))
    assert "B-PROFILE" in script.systems[0] and "A-PROFILE" not in script.systems[0]
    assert "A-PROFILE" in script.systems[1] and "B-PROFILE" not in script.systems[1]
    assert rb.reply and "Priya's AI assistant" in rb.reply  # B shows their first name
    assert ra.reply and ra.reply.startswith("Hi, I'm Ava, an AI assistant.")
    with session_scope() as s:
        by_agent = sorted(m.agent_id for m in s.scalars(select(ReceptionMessage)))
    assert by_agent == sorted([sa.agent_id, sb.agent_id])  # each message is in its own owner's instance
    assert len(a.get("/app/receptionist/messages").json()["messages"]) == 1
    assert len(b.get("/app/receptionist/messages").json()["messages"]) == 1
