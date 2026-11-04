"""A push that never gets through on one channel goes out on the other one the owner linked, the owner can say
they saw it, and a reception note follows the owner's interrupt preference (#491)."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.channels.health import DeliveryFailed
from superaide.identity.models import ChannelIdentity
from superaide.jobs.queue import run_due
from superaide.memory.models import Event
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope
from superaide.proactive import notify
from superaide.proactive.models import NotificationDelivery
from superaide.proactive.notify import notify_owner
from tests.pipeline.test_loop import owner_client  # noqa: F401  (fixture re-export)

pytestmark = pytest.mark.usefixtures("owner_client")  # a signed-up owner who hired Ava

TEXT = "Meera needs you within 5 minutes"


class Messenger:
    """Fails every send while `down`, delivers otherwise."""

    def __init__(self, provider: str, down: bool) -> None:
        self.provider, self.down = provider, down
        self.delivered: list[str] = []

    def send_dm(self, identity: Any, text: str) -> None:
        if self.down:
            raise DeliveryFailed(self.provider, "unreachable", f"{self.provider} is down")
        self.delivered.append(text)


@pytest.fixture
def world(monkeypatch):
    """The owner is linked on Telegram and Slack. `make(telegram_down, slack_down)` returns both fake senders."""
    with session_scope() as s:
        owner = hired_owner(s)
        assert owner is not None
        for provider, key in (("telegram", "1001"), ("slack", "U_OWNER")):
            s.add(ChannelIdentity(provider=provider, stable_key=key, person_id=owner.id, lifecycle="active"))

    def make(telegram_down: bool = True, slack_down: bool = False) -> dict[str, Messenger]:
        senders = {"telegram": Messenger("telegram", telegram_down), "slack": Messenger("slack", slack_down)}
        monkeypatch.setattr(notify, "messenger_for", lambda provider: senders.get(provider))
        monkeypatch.setattr(notify, "confirmed_here", lambda provider, identity: True)
        return senders

    return make


def send(text: str = TEXT, channel: str = "telegram") -> list[str]:
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
        return [(e.meta or {}).get("kind", "") for e in s.scalars(select(Event).order_by(Event.created_at))]


def run_through_the_retries() -> None:
    now = datetime.now(UTC)
    for minutes in (1, 7, 25, 60, 90, 120, 150, 180):  # each pass runs what the last one booked
        run_due(now=now + timedelta(minutes=minutes, seconds=5))


def test_telegram_down_for_the_whole_deadline_fails_over_to_slack_once(world):
    senders = world(telegram_down=True)
    send()
    run_through_the_retries()
    telegram, slack = rows()
    assert (telegram.channel, telegram.state, telegram.attempts) == ("telegram", "rerouted", 4)
    assert (slack.channel, slack.state, slack.attempts) == ("slack", "sent", 1)
    assert slack.meta["failover_of"] == str(telegram.id)
    assert senders["slack"].delivered == [f"I couldn't reach you on Telegram, so I'm sending this here: {TEXT}"]
    assert senders["telegram"].delivered == []
    with session_scope() as s:
        assert notify.undelivered(s) == []  # one push, delivered: nothing is waiting
    assert "delivery_failover" in kinds()
    run_through_the_retries()  # a later pass never sends it twice
    assert len(senders["slack"].delivered) == 1


def test_no_failover_without_another_linked_channel_leaves_it_listed(world):
    senders = world(telegram_down=True)
    with session_scope() as s:
        for i in s.scalars(select(ChannelIdentity).where(ChannelIdentity.provider == "slack")):
            i.lifecycle = "revoked"
    send()
    run_through_the_retries()
    [d] = rows()
    assert d.state == "failed"
    with session_scope() as s:
        assert [x.id for x in notify.undelivered(s)] == [d.id]
    assert senders["slack"].delivered == []


def test_failover_is_one_hop_when_both_channels_are_down(world):
    senders = world(telegram_down=True, slack_down=True)
    send()
    run_through_the_retries()
    telegram, slack = rows()
    assert telegram.state == "rerouted" and slack.channel == "slack" and slack.state == "failed"
    assert len(rows()) == 2  # it does not bounce back to Telegram
    with session_scope() as s:
        assert [x.id for x in notify.undelivered(s)] == [slack.id]  # one open push, on the channel it ended on
    assert senders["telegram"].delivered == senders["slack"].delivered == []


def test_a_telegram_that_was_never_linked_goes_to_slack_at_once(world):
    senders = world(telegram_down=False)
    with session_scope() as s:
        for i in s.scalars(select(ChannelIdentity).where(ChannelIdentity.provider == "telegram")):
            i.lifecycle = "revoked"
    assert send() == ["app", "slack"]
    assert len(senders["slack"].delivered) == 1 and senders["telegram"].delivered == []


def test_failover_off_keeps_the_old_behaviour(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "notify_failover", False)
    senders = world(telegram_down=True)
    send()
    run_through_the_retries()
    [d] = rows()
    assert d.state == "failed" and senders["slack"].delivered == []


def test_a_recovered_channel_does_not_also_fail_over(world):
    senders = world(telegram_down=True)
    send()
    senders["telegram"].down = False  # back before the first retry
    run_through_the_retries()
    [d] = rows()
    assert d.state == "sent" and senders["slack"].delivered == [] and senders["telegram"].delivered == [TEXT]


def test_acknowledging_stops_the_retries_and_clears_the_list(world, owner_client):
    senders = world(telegram_down=True)
    send()
    [d] = rows()
    r = owner_client.post(f"/app/notifications/{d.id}/ack")
    assert r.status_code == 200 and r.json() == {"id": str(d.id), "state": "acked"}
    assert owner_client.post(f"/app/notifications/{d.id}/ack").status_code == 200  # safe to repeat
    run_through_the_retries()
    [d] = rows()
    assert d.state == "acked" and d.acked_at is not None and d.attempts == 1  # no further try
    assert senders["slack"].delivered == senders["telegram"].delivered == []
    assert owner_client.get("/app/notifications/undelivered").json()["count"] == 0


def test_acknowledging_an_unknown_notification_is_404(owner_client):
    import uuid

    assert owner_client.post(f"/app/notifications/{uuid.uuid4()}/ack").status_code == 404


def test_another_owner_cannot_acknowledge_my_notification(world, owner_client):
    from fastapi.testclient import TestClient

    from tests.api_support import OTHER, hire, signup

    world(telegram_down=True)
    send()
    [d] = rows()
    other = TestClient(owner_client.app, headers={"X-Requested-With": "superaide"})
    assert signup(other, **OTHER).status_code == 200
    assert hire(other).status_code == 200
    assert other.post(f"/app/notifications/{d.id}/ack").status_code == 404
    assert rows()[0].state == "queued"


def test_signed_out_cannot_acknowledge(owner_client):
    import uuid

    from fastapi.testclient import TestClient

    anon = TestClient(owner_client.app, headers={"X-Requested-With": "superaide"})
    assert anon.post(f"/app/notifications/{uuid.uuid4()}/ack").status_code == 401
