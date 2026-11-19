# ci-module: reception
"""#477: what makes the owner hear that someone new wrote. One sender can cause at most three notifications an hour
(code's cap, whatever the message says), urgency lifts it only when the owner opted in, quiet hours keep the push
off Telegram and Slack, the deadline is code-read and shown as the sender's claim, and the next owner turn sees the
notification as a sourced system event, never as something to disown."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import time_machine
from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.brain.turn import build_messages
from superaide.memory.knowledge.store import preferences_for
from superaide.memory.models import Event
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from superaide.reception.turn import ReceptionTurn, TakenMessage
from tests.reception.test_notice import _telegram_up
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

T0 = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)  # 13:30 in Asia/Kolkata


def notes() -> list[Event]:
    with session_scope() as s:
        return list(
            s.scalars(select(Event).where(Event.meta["kind"].astext == "message_taken").order_by(Event.created_at))
        )


def _write(ava, clock, minutes: list[int], text: str = "can you call me?") -> None:
    for at in minutes:
        clock.move_to(T0 + timedelta(minutes=at), tick=False)
        ava.handle(hello(text))


def test_one_sender_causes_at_most_three_notes_an_hour_and_nothing_is_lost(owner_world, ava, script):
    with time_machine.travel(T0, tick=False) as clock:
        _write(ava, clock, [0, 16, 32, 48])  # past the 15-minute burst gate each time
    assert len(notes()) == 3
    assert len(taken()) == 4  # the fourth was kept and listed, only the bell was held back
    with session_scope() as s:
        assert s.scalars(select(AuditLog).where(AuditLog.action == "note_capped")).all()


def test_the_cap_is_per_sender_so_a_second_person_still_notifies(owner_world, ava, script):
    with time_machine.travel(T0, tick=False) as clock:
        _write(ava, clock, [0, 16, 32, 48])
        clock.move_to(T0 + timedelta(minutes=50), tick=False)
        ava.handle(hello("hello, it is Rahul", who="U_STRANGER_2"))
    assert len(notes()) == 4


def test_the_cap_resets_after_an_hour(owner_world, ava, script):
    with time_machine.travel(T0, tick=False) as clock:
        _write(ava, clock, [0, 16, 32, 48, 70])
    assert len(notes()) == 4


def _urgent(script) -> None:
    script.out = ReceptionTurn(
        reply="Noted.",
        take_message=TakenMessage(
            name="Meera Iyer", topic="project", message="need an answer", deadline_text="within 5 minutes"
        ),
    )


def test_urgency_does_not_lift_the_cap_without_the_owners_opt_in(owner_world, ava, script):
    with time_machine.travel(T0, tick=False) as clock:
        _write(ava, clock, [0, 16, 32])
        _urgent(script)
        _write(ava, clock, [48], "I need this within 5 minutes")
    assert [r.rank for r in taken()][-1] == "urgent"
    assert len(notes()) == 3


def test_with_the_opt_in_an_urgent_message_notifies_past_the_cap(owner_world, ava, script):
    set_reception(urgent_over_cap=True)
    with time_machine.travel(T0, tick=False) as clock:
        _write(ava, clock, [0, 16, 32])
        _urgent(script)
        _write(ava, clock, [48], "I need this within 5 minutes")
    last = notes()[-1].content
    assert len(notes()) == 4
    assert last.startswith("Message from Meera, someone new, on Slack at 14:18 may be urgent")
    assert "they asked for a reply by 14:23" in last  # read by code, in the owner's time zone, as their claim
    assert "need an answer" not in last


def test_the_opt_in_never_lifts_the_cap_for_ordinary_messages(owner_world, ava, script):
    set_reception(urgent_over_cap=True)
    with time_machine.travel(T0, tick=False) as clock:
        _write(ava, clock, [0, 16, 32, 48])
    assert len(notes()) == 3


def test_a_note_in_quiet_hours_stays_in_the_app(owner_world, ava, script, outbox, monkeypatch):
    _telegram_up(monkeypatch, outbox)
    with session_scope() as s:
        pref = preferences_for(s, hired_owner(s))
        pref.interrupt_channel, pref.quiet_start, pref.quiet_end = "telegram", "13:00", "14:00"  # T0 is 13:30 there
    with time_machine.travel(T0, tick=False):
        ava.handle(hello("can you call me?"))
    assert len(notes()) == 1 and outbox["telegram"].sent == []


def test_outside_quiet_hours_the_same_note_is_pushed(owner_world, ava, script, outbox, monkeypatch):
    _telegram_up(monkeypatch, outbox)
    with session_scope() as s:
        pref = preferences_for(s, hired_owner(s))
        pref.interrupt_channel, pref.quiet_start, pref.quiet_end = "telegram", "00:00", "00:00"  # none
    with time_machine.travel(T0, tick=False):
        ava.handle(hello("can you call me?"))
    assert [m["text"] for m in outbox["telegram"].sent if m.get("stable_key") == "9001"]


def test_the_note_carries_a_source_and_the_next_owner_turn_replays_it_as_a_system_event(owner_world, ava, script):
    ava.handle(hello("can you call me?"))
    [note] = notes()
    assert note.meta["source"].startswith("notification:")
    turns = build_messages([Event(direction="in", content="hi"), note], "did anyone write to me?")
    replay = " ".join(t["content"] for t in turns if t["role"] == "assistant")
    assert "[Notification the app sent to the owner (notification:" in replay and "a real delivery" in replay
    assert "can you call me" not in replay  # still only the fixed text


def test_the_owner_rules_tell_the_model_not_to_disown_a_notification():
    from superaide.brain.loop import OWNER_RULES, OWNER_RULES_SHARED

    assert "never say it did not come from a tool" in OWNER_RULES
    assert "real delivery" not in OWNER_RULES_SHARED  # a room's prompt carries nothing about the owner's chat


def test_the_owner_can_read_and_change_the_opt_in(owner_world):
    c = owner_world["client"]
    assert c.get("/app/receptionist").json()["urgent_over_cap"] is False
    assert c.put("/app/receptionist", json={"urgent_over_cap": True}).json()["urgent_over_cap"] is True
