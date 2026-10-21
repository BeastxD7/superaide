"""Calendar in the daily briefing, and a short prep brief before each meeting. Google is mocked."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.connectors import google
from superaide.db.models import Approval, AuditLog, Event, Job, MemoryItem, Person
from superaide.db.session import session_scope
from superaide.jobs.handlers import load
from superaide.jobs.queue import run_due
from superaide.knowledge.store import preferences_for
from superaide.main import create_app
from superaide.proactive import digest, prep
from superaide.proactive.notify import notify_owner
from superaide.skills import calendar, registry

PASSWORD = "correct horse battery"
load()


def note(s, person_id, text):
    s.add(MemoryItem(content=text, subject_person_ids=[person_id], allowed_kind="persons",
                     allowed_person_ids=[person_id], min_trust=2))  # fmt: skip


def event(event_id: str, title: str, start: datetime, minutes: int = 30, **extra: Any) -> dict[str, Any]:
    return {
        "id": event_id,
        "summary": title,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": (start + timedelta(minutes=minutes)).isoformat()},
        "attendees": [
            {"email": "me@example.com", "self": True},
            {"email": "priya@example.com", "displayName": "Priya"},
        ],
        **extra,
    }


@pytest.fixture
def owner(agent):
    c = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    c.post("/auth/signup", json={"name": "Shashank Rao", "email": "s@example.com", "password": PASSWORD})
    c.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        pref = preferences_for(s, person)
        pref.quiet_start = pref.quiet_end = "00:00"  # never quiet, unless a test says so
    return c


@pytest.fixture
def cal(monkeypatch):
    """Calendar skill installed and Google 'connected'; events come from `cal.events`."""

    class Cal:
        events: list[dict[str, Any]] = []
        calls = 0

    def list_events(token, *, start, end, query="", **_):
        assert token == "tok"
        Cal.calls += 1

        def at(e: dict[str, Any]) -> datetime:
            raw = e["start"].get("dateTime") or e["start"]["date"] + "T12:00:00+08:00"
            return datetime.fromisoformat(raw)

        return [e for e in Cal.events if start <= at(e) < end]

    monkeypatch.setattr(calendar, "connected", lambda: True)
    monkeypatch.setattr(google, "access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(google, "list_events", list_events)
    with session_scope() as s:
        skill = registry.catalog()[calendar.SKILL_ID]
        registry.install(s, calendar.SKILL_ID, skill.manifest.permissions)
    return Cal


def me(s) -> Person:
    person = hired_owner(s)
    assert person is not None
    return person


def preps_sent(s) -> list[Event]:
    return list(s.scalars(select(Event).where(Event.meta["kind"].astext == "meeting_prep")))


def test_the_digest_leads_with_todays_calendar(owner, cal):
    now = datetime.now(UTC)
    with session_scope() as s:
        person = me(s)
        start, _ = digest.day_window(s, person, now, "morning")
        cal.events = [event("e1", "1:1 with Priya", start + timedelta(hours=10))]
        text = digest.compose(s, person, now, "morning")
    assert text is not None and "**Calendar**" in text
    assert "- 10:00–10:30 1:1 with Priya (with Priya)" in text


def test_the_digest_job_schedules_one_prep_per_event(owner, cal):
    with session_scope() as s:
        person = me(s)
        preferences_for(s, person).digest = "morning"
        job = digest.schedule(s, person)
        assert job is not None
        run_at = job.run_at
        start, _ = digest.day_window(s, person, run_at, "morning")
        cal.events = [
            event("e1", "1:1 with Priya", start + timedelta(hours=10)),
            event("e2", "Design review", start + timedelta(hours=14)),
            {"id": "e3", "summary": "Offsite", "start": {"date": f"{start:%Y-%m-%d}"}, "end": {"date": ""}},
        ]
    assert run_due(run_at + timedelta(seconds=1)) == 1
    texts = [m["text"] for m in owner.get("/app/messages").json()]
    assert any("1:1 with Priya" in t and "All day: Offsite" in t for t in texts)
    with session_scope() as s:
        jobs = s.scalars(select(Job).where(Job.kind == prep.JOB_KIND)).all()
        assert sorted(j.payload["event_id"] for j in jobs) == ["e1", "e2"]  # all-day events get no prep
        e1 = next(j for j in jobs if j.payload["event_id"] == "e1")
        assert e1.run_at == start + timedelta(hours=10) - prep.LEAD


def test_not_connected_the_digest_still_goes_without_a_calendar(owner, cal, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not call Google when not connected")

    monkeypatch.setattr(calendar, "connected", lambda: False)
    monkeypatch.setattr(google, "list_events", boom)
    with session_scope() as s:
        s.add(Approval(kind="publish_post", payload={"summary": "x"}))
        s.flush()
        text = digest.compose(s, me(s), datetime.now(UTC), "morning")
    assert text is not None and "**Calendar**" not in text and "Waiting for your OK" in text


def test_google_failing_never_breaks_the_digest(owner, cal, monkeypatch):
    def expired(*a, **k):
        raise google.GoogleError("Google Calendar access has expired")

    monkeypatch.setattr(google, "access_token", expired)
    with session_scope() as s:
        s.add(Approval(kind="publish_post", payload={"summary": "x"}))
        s.flush()
        text = digest.compose(s, me(s), datetime.now(UTC), "morning")
    assert text is not None and "**Calendar**" not in text


def test_skill_not_installed_means_no_calendar_call(owner, monkeypatch):
    monkeypatch.setattr(calendar, "connected", lambda: True)
    monkeypatch.setattr(google, "list_events", lambda *a, **k: pytest.fail("skill is off"))
    with session_scope() as s:
        assert calendar.events_between(s, me(s), datetime.now(UTC), datetime.now(UTC) + timedelta(days=1)) == []


def test_prep_is_scheduled_once_per_event_from_digest_and_scan(owner, cal):
    now = datetime.now(UTC)
    cal.events = [event("e1", "1:1 with Priya", now + timedelta(hours=1))]
    with session_scope() as s:
        person = me(s)
        assert len(prep.schedule_preps(s, person, cal.events, now)) == 1
        assert prep.schedule_preps(s, person, cal.events, now) == []
        scan = prep.schedule_scan(s, person, now)
        assert scan is not None and prep.schedule_scan(s, person, now) is None
        scan_at = scan.run_at
    assert run_due(scan_at + timedelta(seconds=1)) == 1  # the scan sees e1 again, adds nothing
    with session_scope() as s:
        assert len(s.scalars(select(Job).where(Job.kind == prep.JOB_KIND)).all()) == 1
        nxt = s.scalars(select(Job).where(Job.kind == prep.SCAN_KIND, Job.status == "queued")).all()
        assert len(nxt) == 1 and nxt[0].run_at == scan_at + prep.SCAN_EVERY


def test_declined_and_past_events_get_no_prep(owner):
    now = datetime.now(UTC)
    declined = event("d", "Board", now + timedelta(hours=1))
    declined["attendees"][0]["responseStatus"] = "declined"
    with session_scope() as s:
        assert prep.schedule_preps(s, me(s), [declined, event("p", "Old", now - timedelta(hours=1))], now) == []


def run_prep(title: str = "1:1 with Priya") -> None:
    start = datetime.now(UTC) + timedelta(hours=1)
    with session_scope() as s:
        jobs = prep.schedule_preps(s, me(s), [event("e1", title, start)], datetime.now(UTC))
        assert len(jobs) == 1
        run_at = jobs[0].run_at
    assert run_due(run_at + timedelta(seconds=1)) == 1


def test_prep_brief_recalls_notes_about_the_attendees(owner):
    with session_scope() as s:
        note(s, me(s).id, "Priya prefers mornings.")
        note(s, me(s).id, "The dentist is on Main Street.")
    run_prep()
    with session_scope() as s:
        sent = preps_sent(s)
        assert len(sent) == 1
        text = sent[0].content
    assert text.startswith("In 15 min: 1:1 with Priya (") and "with Priya)." in text
    assert "From your notes:\n- Priya prefers mornings" in text and "dentist" not in text


def test_nothing_useful_means_nothing_sent(owner):
    with session_scope() as s:
        note(s, me(s).id, "The dentist is on Main Street.")
        notify_owner(s, me(s), "In 15 min: 1:1 with Priya.", {"kind": "digest"})  # Ava's own words don't count
    run_prep()
    with session_scope() as s:
        assert preps_sent(s) == []
        reasons = [a.detail.get("reason") for a in s.scalars(select(AuditLog)) if a.action == "meeting_prep_skipped"]
        assert reasons == ["nothing_to_say"]


def test_quiet_hours_are_respected(owner):
    start = datetime.now(UTC) + timedelta(hours=1)
    with session_scope() as s:
        person = me(s)
        note(s, person.id, "Priya prefers mornings.")
        pref = preferences_for(s, person)
        local = (start - prep.LEAD).astimezone(digest.zone(pref.timezone))
        pref.quiet_start = (local - timedelta(hours=1)).strftime("%H:%M")
        pref.quiet_end = (local + timedelta(hours=1)).strftime("%H:%M")
        jobs = prep.schedule_preps(s, person, [event("e1", "1:1 with Priya", start)], datetime.now(UTC))
        run_at = jobs[0].run_at
    assert run_due(run_at + timedelta(seconds=1)) == 1
    with session_scope() as s:
        assert preps_sent(s) == []
