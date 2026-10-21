"""Redelivered messages are answered once; threads keep their own conversation."""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
from sqlalchemy import func, select

from superaide.channels import slack, telegram
from superaide.db.models import AuditLog, Context, Event
from superaide.db.session import session_scope
from superaide.memory.store import recent_events
from tests.conftest import MANAGER_SLACK, MANAGER_TG, sl, tg


def events(direction: str | None = None) -> list[Event]:
    with session_scope() as s:
        q = select(Event).order_by(Event.created_at)
        if direction:
            q = q.where(Event.direction == direction)
        rows = list(s.scalars(q))
        s.expunge_all()
        return rows


def test_a_redelivered_message_is_answered_once(agent, manager, outbox):
    msg = replace(tg(MANAGER_TG, "what's on today?"), message_id="881")
    first = agent.handle(msg)
    again = agent.handle(msg)  # Telegram re-poll, Slack retry
    assert first.mode == "known" and again.mode == "duplicate"
    assert len(outbox["telegram"].sent) == 1
    assert [e.external_id for e in events("in")] == ["881"]
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "duplicate_dropped")) == 1


def test_messages_without_a_platform_id_are_never_treated_as_duplicates(agent, manager, outbox):
    agent.handle(tg(MANAGER_TG, "hello"))
    agent.handle(tg(MANAGER_TG, "hello"))
    assert len(outbox["telegram"].sent) == 2


def test_the_same_message_id_in_another_chat_is_a_different_message(agent, manager, outbox):
    agent.handle(replace(tg(MANAGER_TG, "hi"), message_id="5"))
    agent.handle(replace(tg(MANAGER_TG, "hi", chat_kind="group", chat_id="-100"), message_id="5"))
    assert len(events("in")) == 2


def test_a_mention_in_a_slack_channel_is_answered_in_a_thread_under_it(agent, manager, model, outbox):
    model.reply = "On it."
    mention = replace(sl(MANAGER_SLACK, "<@AVA> what's the plan?", chat_kind="group", channel="C1"), message_id="100.1")
    agent.handle(mention)
    assert outbox["slack"].sent[-1] == {"to_context": "C1", "text": "On it.", "thread_id": "100.1"}
    out = events("out")[-1]
    assert out.thread_id == "100.1"


def test_each_slack_thread_keeps_its_own_history(agent, manager, model):
    a1 = replace(sl(MANAGER_SLACK, "thread A: budget", chat_kind="group", channel="C1"), message_id="1.0")
    b1 = replace(sl(MANAGER_SLACK, "thread B: hiring", chat_kind="group", channel="C1"), message_id="2.0")
    agent.handle(a1)
    agent.handle(b1)
    model.systems.clear()
    follow_up = replace(
        sl(MANAGER_SLACK, "and the numbers?", chat_kind="group", channel="C1"), message_id="1.5", thread_id="1.0"
    )
    agent.handle(follow_up)
    with session_scope() as s:
        ctx_id = events("in")[0].context_id
        ctx = s.get(Context, ctx_id)
        assert ctx is not None
        thread_a = [e.content for e in recent_events(s, ctx, thread_id="1.0")]
    assert "thread A: budget" in thread_a and "and the numbers?" in thread_a
    assert not any("thread B" in t for t in thread_a)


def test_slack_and_telegram_parsers_keep_the_platform_ids():
    payload = {
        "team_id": "T_OURS",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D1",
            "user": "U1",
            "text": "hi",
            "ts": "200.2",
            "thread_ts": "200.1",
        },
    }
    msg = slack.parse_event(payload, verified=True, our_team_id="T_OURS")
    assert msg is not None and msg.message_id == "200.2" and msg.thread_id == "200.1"
    payload["event"]["thread_ts"] = "200.2"  # a thread's own first message is not "in" a thread
    msg = slack.parse_event(payload, verified=True, our_team_id="T_OURS")
    assert msg is not None and msg.thread_id == ""

    update = {
        "update_id": 1,
        "message": {
            "message_id": 77,
            "message_thread_id": 12,
            "is_topic_message": True,
            "from": {"id": 1001, "first_name": "S"},
            "chat": {"id": -100, "type": "supergroup"},
            "text": "hi",
        },
    }
    tmsg = telegram.parse_update(update, verified=True)
    assert tmsg is not None and tmsg.message_id == "77" and tmsg.thread_id == "12"


def test_telegram_replies_go_to_the_same_forum_topic():
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    messenger = telegram.TelegramMessenger("123:abc", client=httpx.Client(transport=httpx.MockTransport(handler)))
    messenger.send_to_context(Context(external_id="-100"), "hello", "12")
    messenger.send_to_context(Context(external_id="-100"), "hello")
    assert sent[0]["message_thread_id"] == 12 and "message_thread_id" not in sent[1]


def test_a_new_bot_starts_numbering_again_and_is_not_mistaken_for_a_repeat(agent, manager, outbox):
    def update(text: str, message_id: int) -> dict:
        return {
            "update_id": message_id,
            "message": {
                "message_id": message_id,
                "chat": {"id": int(MANAGER_TG), "type": "private"},
                "from": {"id": int(MANAGER_TG), "first_name": "M"},
                "text": text,
            },
        }

    old = telegram.parse_update(update("hello", 1), verified=True, bot=telegram.bot_id("111:old-secret"))
    new = telegram.parse_update(update("/start", 1), verified=True, bot=telegram.bot_id("222:new-secret"))
    assert old is not None and new is not None
    assert old.message_id == "111:1" and new.message_id == "222:1"
    assert agent.handle(old).mode == "known"
    assert agent.handle(new).mode != "duplicate"  # the owner replaced their bot; Telegram restarted at 1
    assert agent.handle(new).mode == "duplicate"  # a real redelivery from the same bot still is one


def test_the_bot_id_is_the_public_part_of_the_token():
    assert telegram.bot_id("8307766337:AAE-secret-part") == "8307766337"
    assert telegram.bot_id("") == ""
