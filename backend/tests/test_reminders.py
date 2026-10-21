"""Reminders: said in chat or added on the dashboard, delivered once at the right time."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.db.models import ChannelIdentity, Event, Intention, Job, OwnerPreference
from superaide.db.session import session_scope
from superaide.intentions.when import parse_reminder, zone
from superaide.jobs.handlers import load
from superaide.jobs.queue import run_due
from superaide.main import create_app

PASSWORD = "correct horse battery"
SG = zone("Asia/Singapore")
WED_11PM = datetime(2026, 10, 21, 23, 0, tzinfo=SG)

load()


@pytest.mark.parametrize(
    ("said", "task", "due"),
    [
        ("remind me to call Priya on Friday at 5", "Call Priya", "Fri 23 Oct 17:00"),
        ("remind me in 20 minutes to stretch", "Stretch", "Wed 21 Oct 23:20"),
        ("remind me to send the deck tomorrow at 9", "Send the deck", "Thu 22 Oct 09:00"),
        ("Remind me to pay rent next Monday 10am", "Pay rent", "Mon 26 Oct 10:00"),
        ("remind me to book flights on 24 Oct 3pm", "Book flights", "Sat 24 Oct 15:00"),
        ("remind me to water the plants friday", "Water the plants", "Fri 23 Oct 09:00"),
        ("remind me to check the oven at 11:30pm", "Check the oven", "Wed 21 Oct 23:30"),
        (
            "follow up with Arjun about the contract on Tuesday",
            "Follow up with Arjun about the contract",
            "Tue 27 Oct 09:00",
        ),
        ("remind me about the dentist tomorrow morning", "The dentist", "Thu 22 Oct 09:00"),
        ("remind me to email Sam at 9am", "Email Sam", "Thu 22 Oct 09:00"),
        ("please remind me to renew my passport on 3 Nov.", "Renew my passport", "Tue 03 Nov 09:00"),
    ],
)
def test_reminders_are_read_in_the_owners_time_zone(said, task, due):
    parsed = parse_reminder(said, WED_11PM)
    assert parsed is not None
    assert (parsed.task, parsed.due.astimezone(SG).strftime("%a %d %b %H:%M")) == (task, due)


@pytest.mark.parametrize("said", ["remind me to stretch", "what's on tomorrow?", "remind me"])
def test_no_reminder_without_a_task_and_a_time(said):
    assert parse_reminder(said, WED_11PM) is None


@pytest.fixture
def browser(agent):
    app = create_app(agent)
    return lambda: TestClient(app, headers={"X-Requested-With": "superaide"})


@pytest.fixture
def owner(browser):
    c = browser()
    assert (
        c.post(
            "/auth/signup",
            json={"name": "Shashank Rao", "email": "s@example.com", "password": PASSWORD, "timezone": "Asia/Singapore"},
        ).status_code
        == 200
    )
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c


def intentions() -> list[Intention]:
    with session_scope() as s:
        rows = list(s.scalars(select(Intention).order_by(Intention.created_at)))
        s.expunge_all()
        return rows


def deliver_due(at: datetime) -> int:
    return run_due(at + timedelta(seconds=1))


def test_saying_remind_me_in_chat_creates_one_and_confirms_when(owner):
    r = owner.post("/app/chat", json={"text": "remind me to call Priya tomorrow at 5pm"})
    # Before 4am, "tomorrow" means after you sleep, so either word is right depending on when this runs.
    assert r.status_code == 200
    assert re.match(r"Got it\. I'll remind you (today|tomorrow) at 5:00 pm: Call Priya", r.json()["reply"])
    [i] = intentions()
    assert (i.text, i.status, i.source, i.timezone) == ("Call Priya", "open", "chat", "Asia/Singapore")
    with session_scope() as s:
        job = s.scalar(select(Job).where(Job.kind == "deliver_intention"))
        assert job is not None and job.run_at == i.due_at


def test_without_a_time_ava_asks_when_and_creates_nothing(owner):
    r = owner.post("/app/chat", json={"text": "remind me to stretch"})
    assert "When should I remind you?" in r.json()["reply"]
    assert intentions() == []


def test_a_reminder_is_delivered_once_in_the_app(owner):
    owner.post("/app/chat", json={"text": "remind me in 30 minutes to stand up"})
    [i] = intentions()
    assert deliver_due(i.due_at) == 1
    assert deliver_due(i.due_at) == 0  # never twice
    [i] = intentions()
    assert i.delivered_at is not None and i.delivered_via == ["app"]
    texts = [m["text"] for m in owner.get("/app/messages").json()]
    assert texts.count("⏰ Reminder: Stand up") == 1


def test_a_reminder_also_goes_to_telegram_when_linked_and_chosen(owner, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr("superaide.proactive.notify.send_telegram", lambda identity, text: sent.append(text) or True)
    with session_scope() as s:
        person_id = s.scalar(select(Intention.owner_person_id)) or None
    owner.post("/app/chat", json={"text": "remind me in 10 minutes to check the oven"})
    [i] = intentions()
    with session_scope() as s:
        s.add(ChannelIdentity(provider="telegram", stable_key="1001", person_id=i.owner_person_id, lifecycle="active"))
        pref = s.scalar(select(OwnerPreference).where(OwnerPreference.person_id == i.owner_person_id))
        assert pref is not None
        pref.interrupt_channel = "telegram"
    del person_id
    deliver_due(i.due_at)
    assert sent == ["⏰ Reminder: Check the oven"]
    assert intentions()[0].delivered_via == ["app", "telegram"]


def test_dashboard_add_list_snooze_and_finish(owner):
    added = owner.post("/app/reminders", json={"text": "Call the bank tomorrow at 10am"})
    assert added.status_code == 200, added.text
    body = added.json()
    assert body["text"] == "Call the bank" and re.match(r"(today|tomorrow) at 10:00 am", body["due_text"])

    exact = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    owner.post("/app/reminders", json={"text": "Renew insurance", "due_at": exact.isoformat()})
    listed = owner.get("/app/reminders").json()
    assert listed["timezone"] == "Asia/Singapore"
    assert [r["text"] for r in listed["reminders"]] == ["Call the bank", "Renew insurance"]

    rid = body["id"]
    snoozed = owner.post(f"/app/reminders/{rid}/snooze", json={"minutes": 15})
    assert snoozed.status_code == 200
    with session_scope() as s:
        jobs = {j.dedupe_key: j.status for j in s.scalars(select(Job))}
    assert sorted(jobs.values()).count("cancelled") == 1  # the old time won't fire

    assert owner.post(f"/app/reminders/{rid}/done").json()["status"] == "done"
    again = owner.post(f"/app/reminders/{rid}/cancel")
    assert again.status_code == 409
    assert [r["text"] for r in owner.get("/app/reminders").json()["reminders"]] == ["Renew insurance"]
    assert len(owner.get("/app/reminders?all=true").json()["reminders"]) == 2


def test_a_finished_reminder_is_never_delivered(owner):
    r = owner.post("/app/reminders", json={"text": "Water plants in 1 hour"}).json()
    owner.post(f"/app/reminders/{r['id']}/cancel")
    assert deliver_due(datetime.now(UTC) + timedelta(hours=2)) == 0
    assert intentions()[0].delivered_at is None


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"text": "Call mum"}, 422),  # no time anywhere
        ({"text": "Call mum", "due_at": "2020-01-01T09:00:00+08:00"}, 422),  # already passed
        ({"text": "", "due_at": "2030-01-01T09:00:00+08:00"}, 422),
    ],
)
def test_bad_reminders_are_refused_with_a_reason(owner, body, status):
    r = owner.post("/app/reminders", json=body)
    assert r.status_code == status and r.json()["detail"]
    assert intentions() == []


def test_only_the_owner_has_reminders(owner, browser):
    rid = owner.post("/app/reminders", json={"text": "Call Priya in 2 hours"}).json()["id"]
    other = browser()
    other.post("/auth/signup", json={"name": "Priya Nair", "email": "p@example.com", "password": PASSWORD})
    assert other.get("/app/reminders").status_code == 403
    assert other.post(f"/app/reminders/{rid}/done").status_code == 403
    assert browser().get("/app/reminders").status_code == 401


def test_someone_else_saying_remind_me_does_not_create_the_owners_reminder(agent, manager, owner):
    from tests.conftest import MANAGER_TG, tg

    agent.handle(tg(MANAGER_TG, "remind me to call Priya tomorrow at 5"))
    assert intentions() == []
    with session_scope() as s:
        assert s.scalar(select(Event).where(Event.content.like("Got it. I'll remind you%"))) is None


def test_explicit_dates_work_on_the_dashboard_too(owner):
    r = owner.post("/app/reminders", json={"text": "Renew passport on 3 Dec"})
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "Renew passport" and "Dec at 9:00 am" in r.json()["due_text"]


def test_remember_that_with_a_time_and_a_duty_sets_a_reminder_not_a_note(owner):
    # What actually happened on 22 Oct: a canned "Noted." and no reminder.
    r = owner.post("/app/chat", json={"text": "Remember that i need to go to gym at 11:59pm"})
    reply = r.json()["reply"]
    assert reply.startswith("Got it. I'll remind you") and "Go to gym" in reply
    assert "Noted" not in reply
    [i] = intentions()
    assert i.text == "Go to gym"


def test_remember_that_with_a_plain_fact_is_saved_and_says_exactly_what(owner):
    r = owner.post("/app/chat", json={"text": "remember that my flight is on Friday"})
    assert r.json()["reply"] == "Saved to your notebook. Only you can see it."
    assert intentions() == []


def test_tomorrow_just_after_midnight_means_after_you_sleep():
    from superaide.intentions.when import parse_reminder

    late = datetime(2026, 10, 22, 0, 23, tzinfo=SG)  # Thursday, 00:23
    parsed = parse_reminder("remind me to call mum tomorrow evening", late)
    assert parsed is not None and parsed.due.strftime("%a %H:%M") == "Thu 18:00"
