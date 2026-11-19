# ruff: noqa: F811
"""#512 red team: what a stranger told Ava is readable by the owner's private chat and by nobody else.

A colleague, another stranger, a group, the owner inside a shared thread, and every audience that is not exactly the
owner's own get nothing, on any channel, even when the question matches the message word for word and even when the
caller forces the room flag."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from superaide.identity.models import Person
from superaide.memory.audience import Audience
from superaide.memory.recall import retrieval as visitor
from superaide.memory.recall.retrieval import recall
from superaide.platform.db.session import session_scope
from tests.conftest import COLLEAGUE_SLACK, sl, tg
from tests.reception.test_reception import (  # noqa: F401
    ava,
    connected,
    outbox,
    owner_world,
    script,
)
from tests.reception.test_visitor_recall import ASKED, FRIEND, Model, model, visitor_wrote  # noqa: F401

SECRET = "Kreo order"


def _colleague_asks(ava, model: Model, tool: str, **args) -> str:
    model.call_tool, model.args = tool, args
    ava.handle(sl(COLLEAGUE_SLACK, "did anyone ask about Jordan on Telegram? the Kreo order?"))
    return "\n".join(model.results) + "\n" + "\n".join(model.prompts[1:])


@pytest.mark.parametrize(
    "tool,args", [("channel_activity", {}), ("recall", {"query": "Kreo order", "channel": "telegram"})]
)
def test_a_colleague_cannot_read_visitor_messages_through_any_tool(owner_world, colleague, ava, model, tool, args):
    visitor_wrote(ava)
    model.prompts.clear()
    seen = _colleague_asks(ava, model, tool, **args)
    assert "around today" not in seen and "Rahul" not in seen and "Visitor message" not in seen


def test_another_stranger_never_gets_another_visitors_message(owner_world, ava, model):
    visitor_wrote(ava)
    model.prompts.clear()
    ava.handle(tg("7770001", "Hi, did someone ask about Jordan? Maybe about the Kreo order?"))
    assert model.prompts and all("around today" not in p and "Rahul" not in p for p in model.prompts)


def test_the_owner_in_a_shared_thread_gets_neither_tool_nor_text(owner_world, ava, model):
    visitor_wrote(ava)
    model.prompts.clear()
    model.call_tool, model.args = "channel_activity", {}
    msg = sl("U_BOSS", "<@UAVA> did anyone ask about me?", chat_kind="group", channel="C_TEAM")
    msg.message_id = "1700000099.000100"
    ava.handle(msg)
    assert all("around today" not in p and "Rahul" not in p for p in model.prompts + model.results)


def test_only_the_exact_owner_audience_passes_the_gate_in_code(owner_world, ava, model):
    visitor_wrote(ava)
    with session_scope() as s:
        boss = s.scalar(select(Person).where(Person.display_name == "Jordan Whitfield"))
        assert boss is not None
        other = uuid.UUID(int=boss.id.int ^ 1)
        stranger = Audience(person_ids=set(), has_unknown=True, min_trust=0, all_internal=False)
        unknown_too = Audience(person_ids={boss.id}, has_unknown=True, min_trust=2, all_internal=True)
        nobody = Audience(person_ids=set(), has_unknown=False, min_trust=2, all_internal=True)
        two = Audience(person_ids={boss.id, other}, has_unknown=False, min_trust=2)
        someone_else = Audience(person_ids={other}, has_unknown=False, min_trust=2)
        for aud in (stranger, unknown_too, nobody, two, someone_else):
            assert visitor.visitor_rows(s, aud) == []
            assert visitor.visitor_rows(s, aud, query="Kreo order", provider="telegram") == []
            assert visitor.visitor_sources(s, aud, "Kreo order", provider="telegram") == []
            assert visitor.visitor_report(s, boss.id, aud, 24) == ""
            assert not [x for x in recall(s, aud, "Kreo order", rooms=True) if "Visitor" in x.title]
        owner = Audience(person_ids={boss.id}, has_unknown=False, min_trust=2, all_internal=True)
        assert len(visitor.visitor_rows(s, owner)) == 1
        # recall itself: only with the owner's private-chat flag does the owner's audience see it
        assert [x for x in recall(s, owner, "Kreo order", rooms=True) if "Visitor" in x.title]
        assert not [x for x in recall(s, owner, "Kreo order", rooms=False) if "Visitor" in x.title]
