"""ADR 0010 TR8: triage sections in the digest, and the catch-up after quiet hours (behind the flag)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import time_machine
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.config import get_settings
from superaide.db.models import Context, Event, Job, Person
from superaide.db.session import session_scope
from superaide.jobs.handlers import load
from superaide.jobs.queue import run_due
from superaide.main import create_app
from superaide.proactive import digest
from superaide.triage import digest as tdigest
from superaide.triage.models import Escalation, TriageDecision
from superaide.triage.types import POLICY_VERSION

load()
PASSWORD = "correct horse battery"


@pytest.fixture
def owner_id(agent) -> uuid.UUID:
    c = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    c.post("/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": PASSWORD,
                                 "timezone": "Asia/Singapore"})  # fmt: skip
    c.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    with session_scope() as s:
        boss = hired_owner(s)
        assert boss is not None
        return boss.id


@pytest.fixture
def on(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "triage_enabled", True)


def decide(owner_id, decision: str, reason: str, *, name="Priya Tan", hits=(), classifier=None, features=None,
           escalation: str | None = None, acked: bool = False) -> uuid.UUID:  # fmt: skip
    with session_scope() as s:
        p = Person(display_name=name, relation="colleague")
        ctx = Context(provider="slack", tenant_scope="T", external_id=uuid.uuid4().hex, kind="dm",
                      audience_class="private_1to1")  # fmt: skip
        s.add_all([p, ctx])
        s.flush()
        ev = Event(context_id=ctx.id, direction="in", content="(message)", resolved_person_id=p.id)
        s.add(ev)
        s.flush()
        d = TriageDecision(inbound_event_id=ev.id, sender_person_id=p.id, context_id=ctx.id, decision=decision,
                           reason=reason, rule_hits=list(hits), classifier=classifier, features=features or {},
                           stage_at_time="infant", policy_version=POLICY_VERSION)  # fmt: skip
        s.add(d)
        s.flush()
        if escalation:
            s.add(Escalation(decision_id=d.id, owner_person_id=owner_id, state=escalation,
                             acked_at=datetime.now(UTC) if acked else None))  # fmt: skip
        return d.id


def compose(owner_id) -> str | None:
    with session_scope() as s:
        person = s.get(Person, owner_id)
        return digest.compose(s, person, datetime.now(UTC) + timedelta(seconds=1), "morning", events=[])


def test_off_the_digest_is_unchanged(owner_id):
    decide(owner_id, "verify", "unverified request: payment")
    assert compose(owner_id) is None  # nothing else to say, and triage adds nothing


def test_sections_in_order_with_empty_ones_left_out(owner_id, on):
    decide(owner_id, "verify", "unverified request: payment", name="Ravi")
    decide(owner_id, "interrupt", "VIP, and it looks urgent", name="Dana Lee", escalation="acked", acked=True)
    decide(owner_id, "interrupt", "urgent topic", name="Sam Park", escalation="expired")
    decide(owner_id, "hold", "not urgent", classifier={"evidence": ["deck by Friday"], "risk_flags": []})
    decide(owner_id, "hold", "not urgent", name="Ana", classifier={"evidence": ["IBAN 12345"],
                                                                   "risk_flags": ["payment"]})  # fmt: skip
    decide(owner_id, "hold", "automated sender", name="Build bot", hits=["R4_automated"])
    decide(owner_id, "hold", "couldn't classify", name="Lee", hits=["classifier:bad_quote"])
    text = compose(owner_id)
    assert text is not None and text.startswith("Good morning, Jordan.")
    order = [text.index(h) for h in ("**Needs your decision**", "**Escalations**", "**Messages taken**")]
    assert order == sorted(order)
    assert "**Handled for you**" not in text
    assert "Ravi (unverified request)" in text
    assert "Dana Lee: VIP, and it looks urgent (acknowledged)" in text and "(not acknowledged)" in text
    assert "“deck by Friday”" in text and "IBAN" not in text  # never quote money on a channel push
    assert "**FYI:** 1" in text and "**Couldn't sort:** 1" in text


def test_only_since_the_last_digest(owner_id, on):
    decide(owner_id, "verify", "old one", name="Old")
    with session_scope() as s:
        person = s.get(Person, owner_id)
        from superaide.proactive.notify import notify_owner

        notify_owner(s, person, "Good morning", {"kind": "digest", "slot": "morning"}, channel="app")
    decide(owner_id, "verify", "new one", name="New")
    text = compose(owner_id) or ""
    assert "New" in text and "Old" not in text


@pytest.fixture
def daytime():
    """09:00 UTC (17:00 for the Singapore owner above): clear of quiet hours (22:00-07:00).

    The catch-up window starts at the owner's quiet-hours start, so a test that fakes "quiet hours end in
    two hours" on the wall clock drops its own decision whenever 22:00 Singapore time falls in between.
    """
    with time_machine.travel(datetime(2026, 10, 22, 9, 0, tzinfo=UTC), tick=True):
        yield


def test_quiet_hours_schedule_one_catch_up(daytime, owner_id, on):
    with session_scope() as s:
        person = s.get(Person, owner_id)
        end = datetime.now(UTC) + timedelta(hours=2)
        tdigest.schedule_catchup(s, person, end)
        tdigest.schedule_catchup(s, person, end)  # a second message the same night
        jobs = s.scalars(select(Job).where(Job.kind == "triage_catchup")).all()
        assert len(jobs) == 1
    decide(owner_id, "hold", "can wait", name="Night Owl")
    run_due(end + timedelta(seconds=5))
    with session_scope() as s:
        note = s.scalar(select(Event).where(Event.meta["kind"].astext == "triage_catchup"))
        assert note is not None and "While your notifications were quiet" in note.content
        assert "Night Owl" in note.content


def test_an_empty_catch_up_sends_nothing(daytime, owner_id, on):
    with session_scope() as s:
        tdigest.schedule_catchup(s, s.get(Person, owner_id), datetime.now(UTC) + timedelta(minutes=1))
    run_due(datetime.now(UTC) + timedelta(minutes=2))
    with session_scope() as s:
        assert s.scalar(select(Event).where(Event.meta["kind"].astext == "triage_catchup")) is None
