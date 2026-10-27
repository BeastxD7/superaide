"""Issue #474: whoever writes to Ava for the owner knows from the first reply what will happen, nothing they write is
lost, and Ava never says "passed on" for something that was not stored.

Code stores every inbound message before it decides whether to reply; the cap only decides whether Ava answers.
The model is fake, so the proof is what was stored and what was said."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import time_machine
from sqlalchemy import select

from superaide.platform.db.models import Event
from superaide.platform.db.session import session_scope
from superaide.reception import service
from superaide.reception.models import ReceptionMessage
from superaide.reception.turn import ReceptionTurn, TakenMessage
from tests.reception.test_reception import (  # noqa: F401  (fixtures and helpers)
    Receptionist,
    ava,
    connected,
    hello,
    outbox,
    owner_world,
    script,
    set_reception,
    taken,
)


def said(msg) -> str:
    return msg.reply or ""


def test_the_first_reply_states_the_real_hourly_cap_and_only_the_first(owner_world, ava, script):
    first = said(ava.handle(hello("hello?")))
    assert "up to 10 messages an hour" in first and "still pass everything on" in first
    assert "up to 10 messages" not in said(ava.handle(hello("anyone?")))
    set_reception(per_hour=4)
    other = said(ava.handle(hello("hi", who="U_ANOTHER_ONE")))
    assert "up to 4 messages an hour" in other  # the real number from the owner's settings


def test_a_warning_comes_once_when_two_replies_are_left(owner_world, ava, script):
    replies = [said(ava.handle(hello(f"message {i}"))) for i in range(10)]
    near = [i + 1 for i, r in enumerate(replies) if "2 more" in r]
    assert near == [8]  # once, at 80 percent of 10
    assert "still pass everything on" in replies[7]


def test_fifteen_messages_are_all_stored_whatever_the_cap_and_the_model_does(owner_world, ava, script):
    # The model fills `take_message` on some turns and not on others; code stores all fifteen.
    sent = []
    for i in range(15):
        script.out = ReceptionTurn(
            reply="Noted.", take_message=TakenMessage(message=f"structured {i}") if i % 5 == 0 else None
        )
        sent.append(ava.handle(hello(f"message {i}")))
    rows = taken()
    assert len(rows) == 15
    assert {r.message for r in rows if r.source == "code"} >= {"message 1", "message 14"}
    assert {r.message for r in rows if r.source == "model"} == {
        "structured 0",
        "structured 5",
    }  # 10 is past the cap: no model, so no structure
    assert script.calls == 10  # no model call once the cap is reached


def test_at_the_cap_one_truthful_notice_with_the_time_then_quiet(owner_world, ava, script):
    for i in range(10):
        ava.handle(hello(f"message {i}"))
    eleventh = said(ava.handle(hello("message 10")))
    assert "stop replying" in eleventh and "in about" in eleventh and "still passed on" in eleventh
    quiet = [ava.handle(hello(f"message {i}")) for i in range(11, 15)]
    assert all(r.reply is None and r.notes == ["rate_limited"] for r in quiet)  # no empty string, no repeat
    assert len(taken()) == 15


def test_the_cap_sentence_never_reveals_the_daily_limit(owner_world, ava, script):
    set_reception(per_day=7)
    text = said(ava.handle(hello("hello?")))
    assert "7" not in text


def test_an_urgent_message_after_the_cap_is_stored_and_the_owner_is_told(owner_world, ava, script, monkeypatch):
    for i in range(10):
        ava.handle(hello(f"message {i}"))
    ava.handle(hello("message 10"))  # the cap notice
    ava.handle(hello("the server is down and the client call is today at 3pm, please tell him!"))
    rows = taken()
    assert len(rows) == 12 and rows[-1].message.startswith("the server is down")
    with session_scope() as s:
        notes = s.scalars(select(Event).where(Event.meta["kind"].astext == "message_taken")).all()
    assert notes  # the owner got a fixed-text note (what they wrote is never put in the owner's chat)
    assert all("server is down" not in n.content for n in notes)


def test_passed_on_is_never_said_when_nothing_was_stored(owner_world, ava, script, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(service, "store_message", boom)
    script.out = ReceptionTurn(reply="I'll pass that on to him right away.", take_message=None)
    r = said(ava.handle(hello("please call me back")))
    assert "pass that on" not in r and "passed on" not in r and "couldn't save" in r
    for i in range(10):
        ava.handle(hello(f"message {i}"))
    over = said(ava.handle(hello("message 10")))
    assert "passed on" not in over and "couldn't save" in over
    assert taken() == []


def test_a_model_that_forgets_take_message_still_loses_nothing(owner_world, ava, script):
    script.out = ReceptionTurn(reply="I'll pass that on.", take_message=None)
    ava.handle(hello("Tell him the invoice is ready"))
    [m] = taken()
    assert m.source == "code" and m.message == "Tell him the invoice is ready"


def test_after_the_reset_the_bot_replies_again_and_says_so_once(owner_world, ava, script):
    now = datetime.now(UTC)
    with time_machine.travel(now, tick=False) as clock:
        for i in range(11):
            ava.handle(hello(f"message {i}"))
        assert ava.handle(hello("still there?")).reply is None
        clock.shift(timedelta(minutes=61))
        back = said(ava.handle(hello("hello again")))
        assert back.startswith("I can reply again.")
        assert not said(ava.handle(hello("and one more"))).startswith("I can reply again.")
        assert len(taken()) == 14


def test_there_is_a_daily_cap_on_what_is_stored_and_the_sender_is_told_the_truth(owner_world, ava, script, monkeypatch):
    set_reception(per_hour=100, per_day=100)
    monkeypatch.setattr(service, "STORE_PER_DAY", 3)
    replies = [said(ava.handle(hello(f"message {i}"))) for i in range(5)]
    assert len(taken()) == 3
    assert "any more messages from you today" in replies[3]
    assert "passed on" not in replies[3]


def test_the_storage_rule_does_not_leak_between_senders(owner_world, ava, script, monkeypatch):
    monkeypatch.setattr(service, "STORE_PER_DAY", 1)
    ava.handle(hello("first"))
    ava.handle(hello("second"))
    ava.handle(hello("someone else", who="U_ANOTHER_ONE"))
    with session_scope() as s:
        rows = s.scalars(select(ReceptionMessage)).all()
    assert len(rows) == 2 and {r.message for r in rows} == {"first", "someone else"}
