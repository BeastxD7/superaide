"""#640: in the owner's Slack DM a thread continues the conversation it was opened from. Outbound events carry the
platform's message id, so a thread under Ava's own message finds its root; channel and group threads keep their
own audience and history."""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest
from sqlalchemy import select

from superaide.channels import telegram
from superaide.channels.slack import SlackMessenger
from superaide.memory.models import Context, Event
from superaide.memory.store import recent_events
from superaide.platform.db.session import session_scope
from tests.conftest import COLLEAGUE_SLACK, MANAGER_SLACK, MANAGER_TG, sl, tg

QUESTION = "Shall I book the Thai place for Friday?"


class Spy:
    """Wraps the scripted model and keeps what each turn's prompt held."""

    def __init__(self, model) -> None:
        self.model = model
        self.prompts: list[str] = []

    def __call__(self, purpose, system, messages, output_model):
        self.prompts.append(str(messages))
        return self.model(purpose, system, messages, output_model)


@pytest.fixture
def spy(agent, model) -> Spy:
    from superaide.brain.llm import FakeLLM

    s = Spy(model)
    agent.llm = FakeLLM(s)
    return s


def owner(text: str, ts: str, thread: str = ""):
    return replace(sl(MANAGER_SLACK, text), message_id=ts, thread_id=thread)


def events(direction: str | None = None) -> list[Event]:
    with session_scope() as s:
        q = select(Event).order_by(Event.created_at, Event.id)
        if direction:
            q = q.where(Event.direction == direction)
        rows = list(s.scalars(q))
        s.expunge_all()
        return rows


def first_exchange(agent, model) -> str:
    """The owner says something at the top level; Ava answers with a question. Returns Ava's message id."""
    model.reply = QUESTION
    agent.handle(owner("find me a restaurant for Friday", "1.0"))
    [out] = events("out")
    assert out.external_id, "the reply's platform id is stored"
    return out.external_id


def test_everything_at_the_top_level_keeps_all_earlier_turns(agent, manager, model, spy):
    first_exchange(agent, model)
    agent.handle(owner("yes please", "3.0"))
    assert QUESTION in spy.prompts[-1]


def test_a_thread_under_the_owners_own_first_message_still_sees_avas_top_level_reply(agent, manager, model, spy):
    first_exchange(agent, model)
    agent.handle(owner("yes please", "3.0", thread="1.0"))
    assert "find me a restaurant for Friday" in spy.prompts[-1] and QUESTION in spy.prompts[-1]


def test_a_thread_under_avas_message_sees_her_question_and_what_came_before(agent, manager, model, spy):
    ava_ts = first_exchange(agent, model)
    agent.handle(owner("yes please", "3.0", thread=ava_ts))
    prompt = spy.prompts[-1]
    assert QUESTION in prompt and "find me a restaurant for Friday" in prompt


def test_when_every_message_carries_the_same_thread_id_all_earlier_turns_are_there(agent, manager, model, spy):
    model.reply = QUESTION
    agent.handle(owner("find me a restaurant for Friday", "1.0", thread="1.0"))
    agent.handle(owner("yes please", "3.0", thread="1.0"))
    assert QUESTION in spy.prompts[-1] and "find me a restaurant for Friday" in spy.prompts[-1]


def test_outbound_events_carry_the_platform_id_and_a_redelivery_is_still_answered_once(agent, manager, model, outbox):
    agent.handle(owner("hello", "1.0"))
    again = agent.handle(owner("hello", "1.0"))
    assert again.mode == "duplicate"
    assert len(outbox["slack"].sent) == 1
    assert [e.external_id for e in events("in")] == ["1.0"]
    assert all(e.external_id for e in events("out"))


def test_a_reply_that_is_refused_is_stored_with_no_id_and_the_undelivered_mark(agent, manager, outbox, monkeypatch):
    def refused(*_a, **_k):
        raise RuntimeError("channel_not_found")

    monkeypatch.setattr(outbox["slack"], "send_to_context", refused)
    agent.handle(owner("hello", "1.0"))
    [out] = events("out")
    assert out.external_id is None and out.meta.get("undelivered") == "RuntimeError"


def test_slack_returns_the_ts_of_the_first_part_and_telegram_the_message_id():
    class Client:
        def __init__(self) -> None:
            self.n = 0

        def chat_postMessage(self, **kwargs):  # noqa: N802 - slack_sdk's name
            self.n += 1
            return {"ok": True, "ts": f"9.{self.n}"}

    assert SlackMessenger("t", client=Client()).send_to_context(Context(external_id="D1"), "x" * 9000) == "9.1"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 55}})

    tg_messenger = telegram.TelegramMessenger("123:abc", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert tg_messenger.send_to_context(Context(external_id="1001"), "hello") == "55"


def test_a_telegram_dm_reply_is_stored_with_its_message_id(manager, model, settings, monkeypatch):
    from superaide.brain.llm import FakeLLM
    from superaide.pipeline import Agent

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 4242}})

    messenger = telegram.TelegramMessenger("123:abc", client=httpx.Client(transport=httpx.MockTransport(handler)))
    ava = Agent(llm=FakeLLM(model), messengers={"telegram": messenger}, settings=settings)
    ava.handle(tg(MANAGER_TG, "hello"))
    [out] = events("out")
    assert out.external_id == "4242"


def _ctx(s, kind: str, chat: str) -> Context:
    return s.scalars(select(Context).where(Context.external_id == chat, Context.kind == kind)).one()


def test_a_channel_thread_gains_neither_the_channels_top_level_nor_another_thread(agent, manager, model):
    def post(text: str, ts: str, thread: str = ""):
        return replace(sl(MANAGER_SLACK, text, chat_kind="group", channel="C1"), message_id=ts, thread_id=thread)

    agent.handle(post("<@AVA> top level chatter", "1.0"))
    agent.handle(post("<@AVA> thread B: hiring", "2.0"))
    agent.handle(post("and the numbers?", "1.5", thread="1.0"))
    with session_scope() as s:
        ctx = _ctx(s, "group", "C1")
        texts = [e.content for e in recent_events(s, ctx, thread_id="1.0")]
    assert "and the numbers?" in texts and any("top level chatter" in t for t in texts)  # its own root
    assert not any("thread B" in t for t in texts)


def test_another_persons_dm_never_enters_the_owners_thread_history(agent, manager, colleague, model):
    model.reply = "Sure."
    agent.handle(owner("find me a restaurant for Friday", "1.0"))
    other = replace(sl(COLLEAGUE_SLACK, "my private salary question"), message_id="1.1")
    agent.handle(other)
    with session_scope() as s:
        mine = _ctx(s, "dm", f"D_{MANAGER_SLACK}")
        texts = [e.content for e in recent_events(s, mine, thread_id="1.0")]
    assert any("restaurant" in t for t in texts)
    assert not any("salary" in t for t in texts)
