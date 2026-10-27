"""Google Calendar push (events.watch): channels per instance, a verified webhook, renewal, and the
half-hourly scan as the fallback. Google is a mock: never the real one."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from superaide.app.accounts import owner as hired_owner
from superaide.integrations.calendar import skill as calendar
from superaide.integrations.calendar import watch as cw
from superaide.integrations.google import client as google
from superaide.jobs.handlers import load
from superaide.main import create_app
from superaide.platform.config import get_settings
from superaide.platform.db.models import AuditLog, CalendarWatch, Job
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import InstanceScope, use_instance
from superaide.proactive import prep
from superaide.skills import registry

load()
PASSWORD = "correct horse battery"


class FakeGoogle:
    def __init__(self) -> None:
        self.watches: list[dict[str, Any]] = []
        self.stopped: list[dict[str, Any]] = []
        self.refuse = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(google.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "ya29.x", "expires_in": 3600})
        body = json.loads(request.content or b"{}")
        if url.startswith(google.WATCH_URL):
            if self.refuse:
                return httpx.Response(401, json={"error": {"message": "Unauthorized WebHook callback channel"}})
            self.watches.append(body)
            expires = int((datetime.now(UTC) + timedelta(days=7)).timestamp() * 1000)
            return httpx.Response(200, json={"id": body["id"], "resourceId": f"res-{len(self.watches)}",
                                             "expiration": str(expires)})  # fmt: skip
        if url.startswith(google.STOP_URL):
            self.stopped.append(body)
            return httpx.Response(204)
        if "/events" in url:
            return httpx.Response(200, json={"items": []})
        return httpx.Response(404)


@pytest.fixture
def fake(monkeypatch) -> FakeGoogle:
    f = FakeGoogle()
    transport = httpx.MockTransport(f)
    monkeypatch.setattr(google, "_client", lambda client=None: client or httpx.Client(transport=transport))
    google._cache.clear()
    settings = get_settings()
    monkeypatch.setattr(settings, "google_calendar_push", True)
    monkeypatch.setattr(settings, "public_base_url", "https://ava.example.com")
    yield f
    google._cache.clear()


@pytest.fixture
def app(agent):
    return create_app(agent)


def make_owner(app, name: str, email: str) -> InstanceScope:
    c = TestClient(app, headers={"X-Requested-With": "superaide"})
    c.post("/auth/signup", json={"name": name, "email": email, "password": PASSWORD})
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    assert (
        c.post("/app/skills/superaide/google-calendar/install", json={"accepted": ["read_private"]}).status_code == 200
    )
    with session_scope() as s:
        row = s.execute(
            text("SELECT i.tenant_id, i.id FROM agent_instance i JOIN instance_member m ON m.agent_id = i.id "
                 "JOIN user_account u ON u.id = m.user_id WHERE u.email_normalized = :e"),
            {"e": email},
        ).one()  # fmt: skip
    scope = InstanceScope(tenant_id=row[0], agent_id=row[1])
    from superaide.identity.vault import store

    with use_instance(scope), session_scope() as s:
        store.save(s, "google", {"client_id": "1-x.apps.googleusercontent.com", "client_secret": "sec-ret-1234",
                                 "refresh_token": f"1//{email}"}, user_id=None, meta={})  # fmt: skip
    return scope


@pytest.fixture
def a(app, fake) -> InstanceScope:
    return make_owner(app, "Jordan Whitfield", "s@example.com")


def ensure(scope: InstanceScope) -> CalendarWatch | None:
    with use_instance(scope), session_scope() as s:
        row = cw.ensure(s, hired_owner(s))
        if row is not None:
            s.flush()
            s.expunge(row)
        return row


def notify(client: TestClient, row: CalendarWatch, *, token: str | None = None, state: str = "exists",
           resource: str | None = None, channel: str | None = None) -> httpx.Response:  # fmt: skip
    headers = {
        "X-Goog-Channel-ID": channel or row.channel_id,
        "X-Goog-Channel-Token": cw.channel_token(row.channel_id) if token is None else token,
        "X-Goog-Resource-ID": row.resource_id if resource is None else resource,
        "X-Goog-Resource-State": state,
        "X-Goog-Message-Number": "2",
    }
    return client.post("/webhooks/google/calendar", headers=headers)


def scans(scope: InstanceScope) -> list[Job]:
    with use_instance(scope), session_scope() as s:
        rows = s.scalars(select(Job).where(Job.kind == prep.SCAN_KIND, Job.status == "queued")).all()
        for r in rows:
            s.expunge(r)
        return list(rows)


def test_a_watch_is_made_per_instance_with_its_own_token_and_address(a, fake):
    row = ensure(a)
    assert row is not None and row.status == "active" and row.resource_id == "res-1"
    [made] = fake.watches
    assert made["type"] == "web_hook" and made["address"] == "https://ava.example.com/webhooks/google/calendar"
    assert made["id"] == row.channel_id and row.channel_id.startswith(a.agent_id.hex + "-")
    assert made["token"] == cw.channel_token(row.channel_id) and len(made["id"]) <= 64
    assert ensure(a).id == row.id and len(fake.watches) == 1  # one live channel, not one per scan
    with use_instance(a), session_scope() as s:
        renew = s.scalar(select(Job).where(Job.kind == cw.RENEW_KIND))
        assert renew is not None and renew.run_at < row.expires_at - timedelta(hours=23)


def test_a_verified_notification_runs_the_meeting_scan_now(app, a, fake):
    row = ensure(a)
    c = TestClient(app)
    before = len(scans(a))
    assert notify(c, row, state="sync").status_code == 200  # the first message on a new channel
    assert len(scans(a)) == before
    assert notify(c, row).status_code == 200
    assert notify(c, row).status_code == 200  # a burst becomes one scan
    jobs = scans(a)
    assert len(jobs) == before + 1 and jobs[-1].run_at <= datetime.now(UTC) + timedelta(seconds=5)


@pytest.mark.parametrize(
    "bad",
    [
        {"token": "wrong"},
        {"token": ""},
        {"resource": "res-999"},
        {"channel": "not-an-instance-channel"},
        {"channel": "0" * 32 + "-abc"},
    ],
)
def test_unverified_notifications_are_refused_and_audited(app, a, fake, bad):
    row = ensure(a)
    before = len(scans(a))
    r = notify(TestClient(app), row, **bad)
    assert r.status_code == 401 and len(scans(a)) == before
    with session_scope() as s:
        assert s.scalars(select(AuditLog).where(AuditLog.action == "rejected")).all()


def test_one_instances_token_cant_trigger_another_instance(app, a, fake):
    b = make_owner(app, "Priya Tan", "p@example.com")
    row_a, row_b = ensure(a), ensure(b)
    assert row_a.channel_id != row_b.channel_id
    before_a, before_b = len(scans(a)), len(scans(b))
    r = notify(TestClient(app), row_a, token=cw.channel_token(row_b.channel_id))
    assert r.status_code == 401
    # B's real channel id with A's resource: also refused.
    assert notify(TestClient(app), row_b, resource=row_a.resource_id).status_code == 401
    assert notify(TestClient(app), row_b).status_code == 200
    assert len(scans(a)) == before_a and len(scans(b)) == before_b + 1


def test_off_by_default_the_webhook_is_404_and_no_watch_is_made(app, a, fake, monkeypatch):
    monkeypatch.setattr(get_settings(), "google_calendar_push", False)
    assert ensure(a) is None and fake.watches == []
    r = TestClient(app).post("/webhooks/google/calendar", headers={"X-Goog-Channel-ID": "x"})
    assert r.status_code == 404


def test_push_needs_a_public_https_address(a, fake, monkeypatch):
    monkeypatch.setattr(get_settings(), "public_base_url", "http://localhost:8000")
    assert ensure(a) is None and fake.watches == []


def test_renewal_opens_a_new_channel_and_stops_the_old_one(a, fake):
    old = ensure(a)
    with use_instance(a), session_scope() as s:
        job = s.scalar(select(Job).where(Job.kind == cw.RENEW_KIND))
        cw.renew(s, job)
    with use_instance(a), session_scope() as s:
        rows = {r.channel_id: r.status for r in s.scalars(select(CalendarWatch))}
    assert rows[old.channel_id] == "stopped" and list(rows.values()).count("active") == 1
    assert fake.stopped == [{"id": old.channel_id, "resourceId": old.resource_id}]
    assert len(fake.watches) == 2


def test_an_expired_channel_is_replaced_at_the_next_scan(a, fake):
    old = ensure(a)
    with use_instance(a), session_scope() as s:
        s.get(CalendarWatch, old.id).expires_at = datetime.now(UTC) - timedelta(minutes=1)
    new = ensure(a)
    assert new is not None and new.id != old.id and len(fake.watches) == 2


def test_if_google_refuses_the_watch_the_half_hourly_scan_carries_on(a, fake, monkeypatch):
    fake.refuse = True
    with use_instance(a), session_scope() as s:
        owner = hired_owner(s)
        job = Job(kind=prep.SCAN_KIND, payload={"owner_id": str(owner.id)}, run_at=datetime.now(UTC))
        prep.scan(s, job)
    with use_instance(a), session_scope() as s:
        assert s.scalars(select(CalendarWatch)).all() == []
        assert s.scalars(select(AuditLog).where(AuditLog.action == "calendar_watch_failed")).all()
    assert len(scans(a)) >= 1  # the next look is still booked


def test_the_scan_starts_the_watch_and_disconnecting_forgets_it(a, fake):
    with use_instance(a), session_scope() as s:
        owner = hired_owner(s)
        prep.scan(s, Job(kind=prep.SCAN_KIND, payload={"owner_id": str(owner.id)}, run_at=datetime.now(UTC)))
    assert len(fake.watches) == 1
    from superaide.identity.vault import store

    with use_instance(a), session_scope() as s:
        store.remove(s, "google", user_id=None)
    assert ensure(a) is None
    with use_instance(a), session_scope() as s:
        assert [r.status for r in s.scalars(select(CalendarWatch))] == ["stopped"]


def test_skill_removed_means_no_watch(a, fake):
    with use_instance(a), session_scope() as s:
        registry.remove(s, calendar.SKILL_ID)
    assert ensure(a) is None and fake.watches == []
