"""A push to the owner's Telegram that fails is retried by a job and stays visible until it arrives (#491)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.channels.health import DeliveryFailed
from superaide.identity.models import ChannelIdentity
from superaide.jobs.models import Job
from superaide.jobs.queue import run_due
from superaide.memory.models import Event
from superaide.platform.db.session import session_scope
from superaide.proactive import notify
from superaide.proactive.models import NotificationDelivery
from superaide.proactive.notify import notify_owner
from tests.pipeline.test_loop import owner_client  # noqa: F401  (fixture re-export)

pytestmark = pytest.mark.usefixtures("owner_client")  # a signed-up owner who hired Ava


class FlakyTelegram:
    """A messenger that fails the first `fail` sends, then works."""

    def __init__(self, fail: int) -> None:
        self.fail = fail
        self.calls = 0
        self.delivered: list[str] = []

    def send_dm(self, identity: Any, text: str) -> None:
        self.calls += 1
        if self.calls <= self.fail:
            raise DeliveryFailed("telegram", "unreachable", "Telegram is down")
        self.delivered.append(text)


@pytest.fixture
def telegram(monkeypatch):
    def make(fail: int) -> FlakyTelegram:
        fake = FlakyTelegram(fail)
        monkeypatch.setattr(notify, "messenger_for", lambda provider: fake if provider == "telegram" else None)
        monkeypatch.setattr(notify, "confirmed_here", lambda provider, identity: True)
        return fake

    with session_scope() as s:
        owner = hired_owner(s)
        assert owner is not None
        s.add(ChannelIdentity(provider="telegram", stable_key="1001", person_id=owner.id, lifecycle="active"))
    return make


def send(text: str = "Meera needs you within 5 minutes", channel: str = "telegram") -> list[str]:
    with session_scope() as s:
        owner = hired_owner(s)
        assert owner is not None
        return notify_owner(s, owner, text, {"kind": "reception"}, channel=channel)


def rows() -> list[NotificationDelivery]:
    with session_scope() as s:
        out = list(s.scalars(select(NotificationDelivery).order_by(NotificationDelivery.created_at)))
        s.expunge_all()
        return out


def kinds() -> list[str]:
    with session_scope() as s:
        return [e.meta.get("kind", "") for e in s.scalars(select(Event).order_by(Event.created_at))]


def due_in(minutes: int) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes, seconds=5)


def test_a_failed_push_is_retried_and_delivered_exactly_once(telegram):
    fake = telegram(fail=1)
    assert send() == ["app"]
    [d] = rows()
    assert (d.state, d.attempts, d.channel) == ("queued", 1, "telegram")
    assert d.next_attempt_at is not None
    assert fake.delivered == []

    assert run_due(now=due_in(0)) == 0  # not due yet: nothing is sent early
    assert run_due(now=due_in(1)) == 1
    [d] = rows()
    assert (d.state, d.attempts, d.sent_at is not None) == ("sent", 2, True)
    assert fake.delivered == ["Meera needs you within 5 minutes"]  # one push

    assert run_due(now=due_in(30)) == 0  # nothing left to send: no duplicate after recovery
    assert fake.delivered == ["Meera needs you within 5 minutes"]
    with session_scope() as s:
        assert len(list(s.scalars(select(Event).where(Event.content == "Meera needs you within 5 minutes")))) == 1
    assert "delivery_recovered" in kinds()


def test_two_failures_inside_the_old_window_both_leave_a_trace(telegram):
    telegram(fail=99)
    send("first")
    send("second")
    assert kinds().count("delivery_failed") == 2


def test_a_channel_down_for_the_whole_deadline_leaves_a_not_delivered_state(telegram):
    fake = telegram(fail=99)
    send()
    for minutes in (1, 7, 25, 60):
        run_due(now=due_in(minutes))
    [d] = rows()
    assert (d.state, d.attempts) == ("failed", 4)  # the first try plus retries at 1, 5 and 15 minutes
    assert d.last_error  # says why
    assert fake.delivered == []
    with session_scope() as s:
        listed = notify.undelivered(s)
        assert [x.id for x in listed] == [d.id]
        finals = [e.content for e in s.scalars(select(Event)) if (e.meta or {}).get("final")]
        assert len(finals) == 1 and "after 4 tries" in finals[0] and "not delivered" in finals[0]
        # a job that is run again later does nothing: the row is final
    assert run_due(now=due_in(120)) == 0


def test_a_delivered_push_is_never_retried_or_listed(telegram):
    fake = telegram(fail=0)
    assert send() == ["app", "telegram"]
    [d] = rows()
    assert d.state == "sent" and d.attempts == 1
    with session_scope() as s:
        assert notify.undelivered(s) == []
        assert list(s.scalars(select(Job).where(Job.kind == notify.RETRY_JOB))) == []
    assert len(fake.delivered) == 1


def test_an_app_only_message_has_no_delivery_row(telegram):
    telegram(fail=99)
    assert send(channel="app") == ["app"]
    assert rows() == []


def test_a_channel_that_is_not_linked_fails_at_once_and_is_not_retried():
    assert send() == ["app"]
    [d] = rows()
    assert d.state == "failed" and "isn't linked" in d.last_error
    with session_scope() as s:
        assert list(s.scalars(select(Job).where(Job.kind == notify.RETRY_JOB))) == []


def test_note_flood_is_capped_and_summarised(telegram):
    telegram(fail=99)
    for i in range(notify.NOTES_PER_DAY + 3):
        send(f"message {i}")
    ks = kinds()
    assert ks.count("delivery_failed") == notify.NOTES_PER_DAY
    assert ks.count("delivery_summary") == 1  # one summary a day, however many more fail
    with session_scope() as s:
        summary = [e.content for e in s.scalars(select(Event)) if (e.meta or {}).get("kind") == "delivery_summary"]
        assert summary == [f"{notify.NOTES_PER_DAY + 1} notifications are waiting for Telegram. I'll keep trying."]
    assert len(rows()) == notify.NOTES_PER_DAY + 3  # every one still listed


def test_the_undelivered_list_api_shows_waiting_and_failed(telegram, owner_client):  # noqa: F811
    telegram(fail=99)
    send("Meera: within 5 minutes")
    body = owner_client.get("/app/notifications/undelivered").json()
    assert body["count"] == 1
    item = body["items"][0]
    assert (item["state"], item["channel"], item["attempts"]) == ("queued", "telegram", 1)
    assert item["text"] == "Meera: within 5 minutes" and item["next_attempt_at"]
