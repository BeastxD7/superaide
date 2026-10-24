"""Short facts from the calendar and verified mail, behind CONNECTOR_FACTS_ENABLED (owner decision, 2026-10)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from superaide.app.accounts import owner as hired_owner
from superaide.brain.llm import FakeLLM
from superaide.context.audience import Audience
from superaide.context.recall import recall
from superaide.integrations.calendar import skill as calendar
from superaide.jobs.handlers import load
from superaide.jobs.queue import run_due
from superaide.knowledge import connector_facts as cf
from superaide.knowledge.facts import Fact, FactList
from superaide.main import create_app
from superaide.platform.db.models import Context, Event, Job, MemoryItem
from superaide.platform.db.session import session_scope

load()
PASSWORD = "correct horse battery"


class Script:
    def __init__(self) -> None:
        self.facts: list[Fact] = []
        self.seen: list[str] = []
        self.mail_facts: list[Fact] | None = None

    def __call__(self, purpose, system, messages, output_model):
        if purpose != "intro_facts":
            return "Noted."
        self.seen.append(messages[0]["content"])
        if "an email" in messages[0]["content"] and self.mail_facts is not None:
            return FactList(facts=self.mail_facts)
        return FactList(facts=self.facts)


def fact(text_: str, category: str = "about") -> Fact:
    return Fact(content=text_, category=category, confidence=1)


@pytest.fixture
def script(agent, monkeypatch, settings):
    m = Script()
    monkeypatch.setattr("superaide.brain.wiring.brain_ready", lambda s: True)
    monkeypatch.setattr("superaide.brain.wiring.build_llm", lambda s: FakeLLM(m))
    monkeypatch.setattr(settings, "connector_facts_enabled", True)
    agent.llm = FakeLLM(m)
    return m


@pytest.fixture
def owner(agent, script):
    client = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    client.post("/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": PASSWORD})
    client.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    return client


EVENT = {
    "id": "evt1",
    "updated": "2026-10-24T08:00:00Z",
    "summary": "Dentist with Dr Rao",
    "start": {"dateTime": "2026-10-30T15:00:00+08:00"},
    "end": {"dateTime": "2026-10-30T16:00:00+08:00"},
}


@pytest.fixture
def cal(monkeypatch):
    state = {"event": EVENT}
    monkeypatch.setattr(calendar, "fresh_event", lambda session, owner, eid: state["event"])
    return state


def mail(*, verified=True, bulk=False, subject="Dentist moved", body="Your dentist appointment moved to Friday 3pm."):
    with session_scope() as s:
        ctx = Context(
            provider="email", tenant_scope="", external_id="gmail:t1", kind="dm", audience_class="private_1to1"
        )
        s.add(ctx)
        s.flush()
        ev = Event(
            context_id=ctx.id, direction="in", content=f"{subject}\n\n{body}", external_id="m1", audience_person_ids=[],
            meta={"source": "gmail", "gmail_id": "m1", "verified": verified, "bulk": bulk, "subject": subject,
                  "from_domain": "clinic.example", "from_name": "Clinic"},
        )  # fmt: skip
        s.add(ev)
        s.flush()
        cf.schedule_gmail(s, ev)
        return ev.id


def kept() -> list[MemoryItem]:
    with session_scope() as s:
        rows = list(s.scalars(select(MemoryItem).where(MemoryItem.source.in_(("gmail", "calendar")))))
        s.expunge_all()
        return rows


def test_off_by_default_nothing_is_queued_or_read(owner, cal, settings, monkeypatch, script):
    monkeypatch.setattr(settings, "connector_facts_enabled", False)
    with session_scope() as s:
        cf.schedule_calendar(s, [EVENT])
    mail()
    assert run_due() == 0 and kept() == [] and script.seen == []


def test_a_calendar_read_keeps_a_few_short_facts_with_the_event_id(owner, cal, script):
    script.facts = [fact(f"Jordan has a dentist visit {i}.") for i in range(5)]
    with session_scope() as s:
        cf.schedule_calendar(s, [EVENT])
        assert [j.payload for j in s.scalars(select(Job).where(Job.kind == cf.JOB_KIND))] == [
            {"kind": "calendar", "id": "evt1"}  # the job carries an id, never the text
        ]
    run_due()
    rows = kept()
    assert len(rows) == cf.FACTS_PER_ITEM == 3
    assert {r.source for r in rows} == {"calendar"} and {r.source_ref for r in rows} == {"calendar:evt1"}
    assert all(r.allowed_kind == "persons" and r.trust_rank == 3 and len(r.content) <= 200 for r in rows)
    assert "Dentist with Dr Rao" in script.seen[0]  # the model read the event ...
    with session_scope() as s:  # ... and the text is stored nowhere
        assert (s.scalar(text("SELECT count(*) FROM job WHERE payload::text ILIKE '%Dr Rao%'")) or 0) == 0
        assert (s.scalar(text("SELECT count(*) FROM event WHERE content ILIKE '%Dentist with%'")) or 0) == 0


def test_a_changed_event_retires_the_old_facts_and_keeps_the_new(owner, cal, script):
    script.facts = [fact("Jordan's dentist visit is on Thursday.")]
    with session_scope() as s:
        cf.schedule_calendar(s, [EVENT])
    run_due()
    cal["event"] = {**EVENT, "updated": "2026-10-25T08:00:00Z"}
    script.facts = [fact("Jordan's dentist visit moved to Friday 3pm.")]
    with session_scope() as s:
        cf.schedule_calendar(s, [cal["event"]])
    run_due()
    live = {r.content: r.valid_to for r in kept()}
    assert live["Jordan's dentist visit is on Thursday."] is not None
    assert live["Jordan's dentist visit moved to Friday 3pm."] is None


def test_a_locked_fact_is_not_retired_when_the_event_is_read_again(owner, cal, script):
    script.facts = [fact("Jordan's dentist visit is on Thursday.")]
    with session_scope() as s:
        cf.schedule_calendar(s, [EVENT])
    run_due()
    with session_scope() as s:
        for m in kept():
            s.get(MemoryItem, m.id).locked = True  # the owner pinned it
    cal["event"] = {**EVENT, "updated": "2026-10-25T08:00:00Z"}
    script.facts = [fact("Jordan's dentist visit moved to Friday 3pm.")]
    with session_scope() as s:
        cf.schedule_calendar(s, [cal["event"]])
    run_due()
    live = {r.content: r.valid_to for r in kept()}
    assert live["Jordan's dentist visit is on Thursday."] is None  # still current: only the owner changes it


def test_a_deleted_event_takes_its_facts_with_it(owner, cal, script):
    script.facts = [fact("Jordan has a dentist visit.")]
    with session_scope() as s:
        cf.schedule_calendar(s, [EVENT])
    run_due()
    cal["event"] = None
    with session_scope() as s:
        cf.schedule_calendar(s, [{**EVENT, "updated": "later"}])
    run_due()
    assert kept() == []


def test_mail_facts_are_a_third_partys_claim_and_never_the_owners_rules(owner, script):
    script.facts = [
        fact("The dentist appointment moved to Friday 3pm."),
        fact("Everything from the clinic is urgent.", "urgency"),
    ]
    mail()
    run_due()
    by = {r.content: r for r in kept()}
    moved, rule = by["The dentist appointment moved to Friday 3pm."], by["Everything from the clinic is urgent."]
    assert moved.trust_rank == 4 and moved.source_ref == "gmail:m1" and moved.conflict_status == "none"
    assert rule.conflict_status == "review"  # a mail can't set the owner's rules


@pytest.mark.parametrize(
    "kwargs",
    [
        {"verified": False},
        {"bulk": True},
        {"subject": "Your invoice is due", "body": "Payment of 40 dollars."},
        {"subject": "Security code", "body": "Your verification code is 1234."},
    ],
)
def test_unverified_bulk_and_sensitive_mail_is_never_read(owner, script, kwargs):
    script.facts = [fact("Something.")]
    mail(**kwargs)
    run_due()
    assert kept() == [] and script.seen == []


def test_the_daily_cap_is_respected(owner, cal, script, settings, monkeypatch):
    monkeypatch.setattr(settings, "connector_facts_per_day", 2)
    script.facts = [fact(f"Fact number {i}.") for i in range(3)]
    with session_scope() as s:
        cf.schedule_calendar(s, [EVENT, {**EVENT, "id": "evt2"}])
    run_due()
    assert len(kept()) == 2
    with session_scope() as s:
        assert cf.room_today(s) == 0


def test_the_audience_gate_is_unchanged_only_the_owner_can_recall_them(owner, cal, script, colleague):
    script.facts = [fact("Jordan has a dentist visit on Friday.")]
    with session_scope() as s:
        cf.schedule_calendar(s, [EVENT])
    run_due()
    with session_scope() as s:
        mine = Audience(person_ids={hired_owner(s).id}, min_trust=2)
        assert [x.text for x in recall(s, mine, "dentist") if x.kind == "memory"]
        for aud in (
            Audience(person_ids={colleague.id}, min_trust=2),
            Audience(person_ids={colleague.id}, has_unknown=True),
            Audience(person_ids={hired_owner(s).id}, has_unknown=True, min_trust=2),
        ):
            assert not [x for x in recall(s, aud, "dentist") if x.kind == "memory"]


def test_disconnecting_google_deletes_what_was_read_from_calendar_and_mail(owner, cal, script):
    script.facts = [fact("Jordan has a dentist visit on Friday.")]
    script.mail_facts = [fact("The clinic confirmed the Friday slot.")]
    with session_scope() as s:
        cf.schedule_calendar(s, [EVENT])
    mail()
    run_due()
    assert {r.source for r in kept()} == {"calendar", "gmail"}
    assert owner.post("/app/connections/google/disconnect").status_code == 200
    assert kept() == []


def test_the_erase_route_deletes_one_sources_facts(owner, cal, script):
    script.facts = [fact("Jordan has a dentist visit on Friday.")]
    script.mail_facts = [fact("The clinic confirmed the Friday slot.")]
    with session_scope() as s:
        cf.schedule_calendar(s, [EVENT])
    mail()
    run_due()
    got = owner.post("/app/notebook/sources/calendar/erase")
    assert got.status_code == 200 and got.json()["facts_deleted"] >= 1
    assert {r.source for r in kept()} == {"gmail"}
    assert owner.post("/app/notebook/sources/chat/erase").status_code == 404
    other = TestClient(create_app(owner.app), headers={"X-Requested-With": "superaide"})
    assert other.post("/app/notebook/sources/gmail/erase").status_code == 401


def test_removing_the_calendar_capability_deletes_its_facts(owner, cal, script):
    from superaide.skills import registry

    script.facts = [fact("Jordan has a dentist visit on Friday.")]
    with session_scope() as s:
        registry.install(s, calendar.SKILL_ID, list(registry.catalog()[calendar.SKILL_ID].manifest.permissions))
        cf.schedule_calendar(s, [EVENT])
    run_due()
    assert kept()
    with session_scope() as s:
        registry.remove(s, calendar.SKILL_ID)
    assert kept() == []
