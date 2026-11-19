# ci-module: reception
"""#503: when the model, the budget or a channel fails, Ava degrades honestly. Chaos tests with the fake model and
messenger: every inbound message is stored, every outbound reply is sent once or marked not delivered, no record
claims a delivery that did not happen, and the owner sees a status instead of finding out later."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import time_machine
from sqlalchemy import select

from superaide.brain.spend.budget import BudgetExceeded
from superaide.channels.base import FakeMessenger
from superaide.channels.health import DeliveryFailed
from superaide.jobs.models import Job
from superaide.jobs.queue import run_due
from superaide.memory.models import Event
from superaide.platform.db.session import session_scope
from superaide.reception import resilience, service
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

T0 = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)


class Flaky(FakeMessenger):
    """A channel that refuses every send while `down`, the way an outage does."""

    down = True
    refused = 0

    def send_to_context(self, context, text, thread_id="", sources=None):
        if self.down:
            self.refused += 1
            raise DeliveryFailed("slack", "unreachable", "slack is unreachable")
        return super().send_to_context(context, text, thread_id, sources)


def status() -> dict | None:
    with session_scope() as s:
        return resilience.status(s)


def jobs() -> list[Job]:
    with session_scope() as s:
        return list(s.scalars(select(Job).where(Job.kind == resilience.JOB).order_by(Job.created_at)))


def visitor_replies() -> list[Event]:
    with session_scope() as s:
        return list(s.scalars(select(Event).where(Event.direction == "out", Event.content.contains("Hi, I'm Ava"))))


# --- the model fails ----------------------------------------------------------------------------


def test_a_model_outage_stores_the_message_answers_in_fixed_text_and_shows_the_owner_a_status(owner_world, ava, script):
    assert status() is None  # nothing to say while everything works
    script.fail = RuntimeError("provider overloaded")
    r = ava.handle(hello("Please tell him the invoice is ready, Meera here"))
    assert r.reply is not None and service.FALLBACK in r.reply and "passed your message on" in r.reply
    [m] = taken()
    assert m.source == "code" and m.message == "Please tell him the invoice is ready, Meera here"
    st = status()
    assert st is not None and st["level"] == "basic" and st["reasons"] == ["model"]
    assert st["waiting_messages"] == 1 and "1 message waiting" in st["line"]
    assert "invoice" not in st["line"] and "Meera" not in st["line"]  # fixed words only, never the visitor's


def test_the_status_clears_when_the_model_is_back_and_after_a_quiet_half_hour(owner_world, ava, script):
    script.fail = RuntimeError("provider overloaded")
    ava.handle(hello("one"))
    assert status() is not None
    script.fail = None
    ava.handle(hello("two"))
    assert status() is None  # a good answer clears it at once
    script.fail = RuntimeError("provider overloaded")
    with time_machine.travel(T0, tick=False):
        ava.handle(hello("three"))
        assert status() is not None
    with time_machine.travel(T0 + timedelta(minutes=31), tick=False):
        assert status() is None  # a failure not seen again is forgotten


def test_a_spent_budget_is_named_as_the_budget_and_every_message_is_still_kept(owner_world, ava, script):
    script.fail = BudgetExceeded("today's receptionist limit", "bucket")
    for text in ("first", "second", "third"):
        ava.handle(hello(text))
    assert [m.message for m in taken()] == ["first", "second", "third"]
    st = status()
    assert st is not None and st["reasons"] == ["budget"] and st["waiting_messages"] == 3
    assert "spending limit" in st["line"]


def test_the_owner_sees_the_status_in_the_app(owner_world, ava, script):
    c = owner_world["client"]
    assert c.get("/app/me").json()["degraded"] is None
    script.fail = RuntimeError("provider overloaded")
    ava.handle(hello("hello?"))
    degraded = c.get("/app/me").json()["degraded"]
    assert degraded["level"] == "basic" and "AI model is unavailable" in degraded["line"]


def test_a_pause_is_its_own_level(owner_world, ava):
    from superaide.policy.killswitch import set_frozen

    with session_scope() as s:
        set_frozen(s, True, actor="test")
    st = status()
    assert st is not None and st["level"] == "paused" and "Paused" in st["line"]


# --- a channel fails ----------------------------------------------------------------------------


def test_a_reply_that_could_not_be_sent_is_queued_and_goes_out_once_when_the_channel_heals(
    owner_world, ava, script, outbox, monkeypatch
):
    flaky = Flaky()
    outbox["slack"] = ava.messengers["slack"] = flaky
    _telegram_up(monkeypatch, outbox)
    with time_machine.travel(T0, tick=False):
        r = ava.handle(hello("Is anyone there?"))
        assert r.reply is not None and flaky.sent == []
        [stored] = visitor_replies()
        assert stored.meta["undelivered"] == "unreachable"  # the record never claims it was delivered
        [job] = jobs()
        assert job.status == "queued" and job.payload["text"] == r.reply
        st = status()
        assert st is not None and st["queued_replies"] == 1 and "1 reply queued to send" in st["line"]
        assert run_due(now=T0 + timedelta(seconds=60)) == 1  # still down: stays queued, with back-off
        assert jobs()[0].status == "queued" and flaky.sent == []

    flaky.down = False
    with time_machine.travel(T0 + timedelta(minutes=5), tick=False):
        assert run_due(now=T0 + timedelta(minutes=5)) == 1
        assert [m["text"] for m in flaky.sent if m.get("to_context")] == [r.reply]
        assert run_due(now=T0 + timedelta(minutes=30)) == 0  # drained once: nothing is sent twice
    assert jobs()[0].status == "done"
    with session_scope() as s:
        ev = s.get(Event, stored.id)
        assert ev is not None and ev.meta.get("delivered_late") is True and "undelivered" not in ev.meta
    assert status() is None


def test_a_channel_that_never_heals_ends_as_not_delivered_and_is_listed(owner_world, ava, script, outbox, monkeypatch):
    flaky = Flaky()
    outbox["slack"] = ava.messengers["slack"] = flaky
    _telegram_up(monkeypatch, outbox)
    with time_machine.travel(T0, tick=False):
        ava.handle(hello("Is anyone there?"))
    for i in range(1, resilience.MAX_ATTEMPTS + 2):
        with time_machine.travel(T0 + timedelta(hours=2 * i), tick=False):
            run_due()
    [job] = jobs()
    assert job.status == "failed" and job.attempts == resilience.MAX_ATTEMPTS
    [stored] = visitor_replies()
    assert stored.meta["undelivered"] == "unreachable" and stored.meta["undelivered_final"] is True
    assert "delivered_late" not in stored.meta
    assert [m for m in flaky.sent if m.get("to_context")] == []
    with time_machine.travel(T0 + timedelta(hours=40), tick=False):
        st = status()
    assert st is not None and st["failed_replies"] == 1 and "could not be delivered" in st["line"]


def test_the_message_is_stored_even_when_nothing_can_be_sent(owner_world, ava, script, outbox):
    outbox["slack"] = ava.messengers["slack"] = Flaky()
    r = ava.handle(hello("Please call me back"))
    assert r.reply is not None
    assert [m.message for m in taken()] == ["Please call me back"]


def test_a_reply_to_the_owner_is_not_queued(owner_world, ava, script, outbox):
    """Only visitors' replies are retried: the owner's own chat lives in the app, and a late answer would be stale."""
    from tests.conftest import sl

    outbox["slack"] = ava.messengers["slack"] = Flaky()
    ava.handle(sl("U_BOSS", "hello"))
    assert jobs() == []


def test_the_time_in_the_status_is_the_owners_own_clock(owner_world, ava, script):
    with time_machine.travel(T0, tick=False):  # 08:00 UTC is 13:30 in Asia/Kolkata (the owner's zone)
        script.fail = RuntimeError("provider overloaded")
        ava.handle(hello("hello?"))
        st = status()
    assert st is not None and "since 13:30" in st["line"] and "UTC" not in st["line"]
