"""The daily briefing: today's reminders, dates coming up, approvals — or nothing at all."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.db.models import Approval, Event, Job, MemoryItem
from superaide.db.session import session_scope
from superaide.intentions import service as intentions
from superaide.intentions.when import zone
from superaide.jobs.handlers import load
from superaide.jobs.queue import run_due
from superaide.knowledge.store import preferences_for
from superaide.main import create_app
from superaide.proactive import digest

PASSWORD = "correct horse battery"
SG = zone("Asia/Singapore")
load()


def note(s, person_id, text):
    s.add(MemoryItem(content=text, subject_person_ids=[person_id], allowed_kind="persons",
                     allowed_person_ids=[person_id], min_trust=2))  # fmt: skip


@pytest.mark.parametrize(
    ("text", "label", "when"),
    [
        ("Priya's birthday is on 14 November.", "Priya's birthday", date(2026, 11, 14)),
        ("Mum's birthday is November 16th", "Mum's birthday", date(2026, 11, 16)),
        ("Our anniversary: 12 Nov", "Anniversary", date(2026, 11, 12)),
    ],
)
def test_dates_in_notes_are_found(text, label, when):
    found = digest.upcoming_dates([MemoryItem(content=text)], date(2026, 11, 10))
    assert [(u.label, u.when) for u in found] == [(label, when)]


def test_dates_too_far_away_or_past_this_year_wrap_or_wait():
    notes = [MemoryItem(content="Priya's birthday is on 14 November."), MemoryItem(content="Sam's birthday is 2 Jan")]
    assert digest.upcoming_dates(notes, date(2026, 10, 22)) == []  # 23 days away
    assert [u.when for u in digest.upcoming_dates(notes, date(2026, 12, 30))] == [date(2027, 1, 2)]


def test_next_run_follows_the_preference_and_time_zone():
    now = datetime(2026, 10, 22, 7, 0, tzinfo=SG)
    assert digest.next_run("morning", "Asia/Singapore", now) == (datetime(2026, 10, 22, 8, 0, tzinfo=SG), "morning")
    at, slot = digest.next_run("both", "Asia/Singapore", now.replace(hour=12)) or (None, None)
    assert slot == "evening" and at.hour == 18
    assert digest.next_run("none", "Asia/Singapore", now) is None


@pytest.fixture
def owner(agent):
    c = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    c.post("/auth/signup", json={"name": "Shashank Rao", "email": "s@example.com", "password": PASSWORD})
    c.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    return c


def test_a_morning_digest_lists_today_dates_with_context_and_approvals(owner):
    now = datetime.now(UTC).astimezone(SG)
    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        soon = now.date() + timedelta(days=3)
        note(s, person.id, f"Priya's birthday is on {soon.day} {soon:%B}.")
        note(s, person.id, "Priya is really into pottery lately.")
        intentions.create(
            s,
            person,
            text="Call the bank",
            due_at=now.replace(hour=23, minute=0) if now.hour < 23 else now + timedelta(minutes=5),
        )
        s.add(Approval(kind="publish_post", payload={"summary": "x"}))
        s.flush()
        text = digest.compose(s, person, now, "morning")
    assert text is not None and text.startswith("Good morning, Shashank.")
    assert "Call the bank" in text and "**Today**" in text
    assert "Priya's birthday in 3 days" in text and "really into pottery" in text
    assert "**Waiting for your OK:** 1" in text


def test_nothing_worth_saying_sends_nothing(owner):
    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        assert digest.compose(s, person, datetime.now(UTC), "morning") is None


def test_the_digest_job_sends_once_and_schedules_the_next(owner):
    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        preferences_for(s, person).digest = "morning"
        s.add(Approval(kind="publish_post", payload={"summary": "x"}))
        job = digest.schedule(s, person)
        assert job is not None and job.payload["slot"] == "morning"
        run_at = job.run_at
    assert run_due(run_at + timedelta(seconds=1)) == 1
    texts = [m["text"] for m in owner.get("/app/messages").json()]
    assert any(t.startswith("Good morning, Shashank.") for t in texts)
    with session_scope() as s:
        queued = s.scalars(select(Job).where(Job.kind == "send_digest", Job.status == "queued")).all()
        assert len(queued) == 1 and queued[0].run_at > run_at  # tomorrow's
        assert s.scalar(select(Event).where(Event.meta["kind"].astext == "digest")) is not None
