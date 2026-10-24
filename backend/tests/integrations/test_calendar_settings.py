# ruff: noqa: F811
"""#433: how far ahead Ava reads, her day's hours and how many events she learns from are the owner's settings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from superaide.app.accounts import owner as hired_owner
from superaide.integrations.calendar import settings as cal_settings
from superaide.integrations.calendar import skill as calendar
from superaide.integrations.google import client as google
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from superaide.skills import registry
from tests.integrations.gmail_fake import owner_client_fixture  # noqa: F401


@pytest.fixture
def cal(owner_client, monkeypatch):
    skill = registry.catalog()[cal_settings.SKILL_ID]
    with session_scope() as s:
        row = registry.install(s, cal_settings.SKILL_ID, skill.manifest.permissions)
        row.settings = {}
    seen: dict = {}
    monkeypatch.setattr(calendar, "connected", lambda: True)

    def fetch(session, owner, start, end, query=""):
        seen["span"] = end - start
        return [
            {
                "id": "e1",
                "summary": "Standup",
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": end.isoformat()},
            }
        ]

    monkeypatch.setattr(calendar, "fetch", fetch)
    monkeypatch.setattr(google, "free_busy", lambda token, start, end: [])
    monkeypatch.setattr(calendar, "_token", lambda: "t")
    return seen


def run(fn, args):
    with session_scope() as s:
        return fn(s, hired_owner(s), args)


def test_defaults_are_todays_behaviour(cal):
    with session_scope() as s:
        assert cal_settings.load(s) == {"ahead_days": 14, "day_start": 8, "day_end": 20, "events_per_read": 10}
    out, err = run(calendar.calendar_events, {"days": 30})
    assert not err and cal["span"] == timedelta(days=14)  # the old fixed 14-day cap
    assert "Only" not in str(out[-1]) or "14 days" in str(out[-1])


def test_the_window_follows_the_setting_and_says_which_setting_cut_it_short(owner_client, cal, monkeypatch):
    assert owner_client.put("/app/calendar/read-settings", json={"ahead_days": 7}).status_code == 200
    out, err = run(calendar.calendar_events, {"days": 20})
    assert cal["span"] == timedelta(days=7)
    last = out[-1]["text"]
    assert (
        "Only 7 days were read" in last
        and "How far ahead Ava reads" in last
        and "/dashboard/capabilities#calendar" in last
    )
    assert owner_client.put("/app/calendar/read-settings", json={"ahead_days": 30}).status_code == 200
    run(calendar.calendar_events, {"days": 20})
    assert cal["span"] == timedelta(days=20)


def test_free_time_uses_the_owners_day(owner_client, cal):
    assert owner_client.put("/app/calendar/read-settings", json={"day_start": 9, "day_end": 18}).status_code == 200
    out, _ = run(calendar.calendar_free_busy, {"days": 1})
    assert "09:00–18:00" in out


def test_bad_values_are_refused_unknown_fields_too_and_changes_are_audited(owner_client, cal):
    url = "/app/calendar/read-settings"
    assert owner_client.put(url, json={"ahead_days": 5}).status_code == 422
    assert owner_client.put(url, json={"nonsense": 1}).status_code == 422
    assert owner_client.put(url, json={"day_end": 22}).status_code == 200
    with session_scope() as s:
        rows = [a for a in s.query(AuditLog).all() if a.action == "calendar_read_settings"]
    assert rows and "day_end" in str(rows[-1].detail)
    out = owner_client.get(url).json()
    assert set(out["choices"]) == set(cal_settings.CHOICES) and out["settings"]["day_end"] == 22


def test_the_facts_job_reads_as_many_events_as_the_setting_allows(owner_client, cal, monkeypatch):
    from superaide.knowledge import connector_facts

    monkeypatch.setattr(connector_facts, "enabled", lambda: True)
    queued = []
    monkeypatch.setattr(connector_facts, "enqueue", lambda *a, **k: queued.append(a[2]["id"]))
    events = [{"id": f"x{i}"} for i in range(25)]
    with session_scope() as s:
        connector_facts.schedule_calendar(s, events)
    assert len(queued) == 10
    owner_client.put("/app/calendar/read-settings", json={"events_per_read": 5})
    queued.clear()
    with session_scope() as s:
        connector_facts.schedule_calendar(s, events)
    assert len(queued) == 5
    _ = datetime.now(UTC)


def test_each_setting_says_when_it_was_last_changed(owner_client, cal):
    """#441: the shared setting row shows "last changed by/when" for every connector setting, from the audit log."""
    url = "/app/calendar/read-settings"
    assert owner_client.get(url).json()["last_changed"] == {}
    assert owner_client.put(url, json={"ahead_days": 7}).status_code == 200
    changed = owner_client.get(url).json()["last_changed"]
    assert set(changed) == {"ahead_days"} and changed["ahead_days"]["by"] == "you"
