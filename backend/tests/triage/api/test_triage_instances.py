"""ADR 0010 with ADR 0009 T5: triage, escalation and catch-up jobs are queued in the owner's
instance and run there, so every row they write belongs to that instance and no other."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import time_machine
from sqlalchemy import select

from superaide.identity.auth.models import UserAccount
from superaide.identity.models import ChannelIdentity, Person
from superaide.identity.trust import Trust
from superaide.jobs.handlers import load
from superaide.jobs.models import Job
from superaide.jobs.queue import run_due
from superaide.memory.knowledge.store import preferences_for
from superaide.memory.models import Contact, Context, Event
from superaide.platform.config import get_settings
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import InstanceScope, use_instance
from superaide.triage import digest as tdigest
from superaide.triage import service
from superaide.triage.models import Escalation, EscalationDelivery, TriageDecision
from tests.api_support import OTHER, OWNER, hire, signup

load()


def scope_of(email: str) -> tuple[InstanceScope, Person]:
    from superaide.app.accounts import instance_of

    with session_scope() as s:
        user = s.scalar(select(UserAccount).where(UserAccount.email_normalized == email))
        assert user is not None
        found = instance_of(s, user.id)
        assert found is not None
        scope, person_id = found
        person = s.get(Person, person_id)
        assert person is not None
        s.expunge(person)
        return scope, person


def vip_message(scope: InstanceScope, owner: Person, text: str) -> Event:
    """A verified VIP colleague's Slack message, logged in this instance."""
    with use_instance(scope), session_scope() as s:
        dana = Person(display_name="Dana Lee", relation="colleague")
        s.add(dana)
        s.flush()
        key = f"U_DANA_{scope.agent_id.hex[:6]}"
        ident = ChannelIdentity(person_id=dana.id, provider="slack", tenant_scope="T_OURS", stable_key=key,
                                trust_level=int(Trust.LINKED))  # fmt: skip
        ctx = Context(provider="slack", tenant_scope="T_OURS", external_id=f"D_{scope.agent_id.hex[:8]}", kind="dm",
                      audience_class="private_1to1")  # fmt: skip
        s.add_all([ident, ctx, Contact(owner_person_id=owner.id, person_id=dana.id, vip=True)])
        s.flush()
        preferences_for(s, owner).urgent_topics = ["server down"]
        ev = Event(context_id=ctx.id, direction="in", content=text, resolved_person_id=dana.id,
                   sender_identity_id=ident.id, trust_at_time=int(Trust.LINKED))  # fmt: skip
        s.add(ev)
        s.flush()
        service.schedule(s, ev)
        s.expunge(ev)
        return ev


@pytest.fixture
def daytime():
    """09:00 UTC (17:00 for the Singapore owners these tests sign up): well clear of quiet hours (22:00-07:00).

    The catch-up window is built from the owner's own clock, so a test that runs on the wall clock fails
    whenever the owner's quiet-hours boundary falls inside it (CI hit 21:59 Singapore time). Ask for this
    before `two` so the sign-ups happen at the frozen time too.
    """
    with time_machine.travel(datetime(2026, 10, 22, 9, 0, tzinfo=UTC), tick=True):
        yield


@pytest.fixture
def two(browser, monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)
    a, b = browser(), browser()
    assert signup(a).status_code == 200 and hire(a).status_code == 200
    assert signup(b, **OTHER).status_code == 200 and hire(b).status_code == 200
    (sa, pa), (sb, pb) = scope_of(OWNER["email"]), scope_of(OTHER["email"])
    assert sa.agent_id != sb.agent_id
    return sa, pa, sb, pb


def drain(at: datetime | None = None) -> None:
    for _ in range(20):
        if run_due(at) == 0:
            return


def test_triage_runs_in_the_owners_instance(two):
    sa, pa, sb, pb = two
    ev = vip_message(sb, pb, "The server is down")
    with session_scope() as s:
        job = s.scalar(select(Job).where(Job.kind == "triage_inbound"))
        assert job is not None and job.agent_id == sb.agent_id  # queued in B's scope
    drain()
    with session_scope() as s:
        d = s.scalar(select(TriageDecision).where(TriageDecision.inbound_event_id == ev.id))
        assert d is not None and d.decision == "interrupt" and d.agent_id == sb.agent_id
        esc = s.scalar(select(Escalation).where(Escalation.decision_id == d.id))
        assert esc is not None and esc.agent_id == sb.agent_id and esc.owner_person_id == pb.id
        assert {x.agent_id for x in s.scalars(select(EscalationDelivery))} == {sb.agent_id}
        steps = s.scalars(select(Job).where(Job.kind == "escalation_step")).all()
        assert steps and {j.agent_id for j in steps} == {sb.agent_id}
        note = s.scalar(select(Event).where(Event.meta["kind"].astext == "escalation"))
        assert note is not None and note.agent_id == sb.agent_id  # B's app, not A's
        assert s.scalar(select(TriageDecision).where(TriageDecision.agent_id == sa.agent_id)) is None
        audits = s.scalars(select(AuditLog).where(AuditLog.action == "decided")).all()
        assert audits and {a.agent_id for a in audits} == {sb.agent_id}


def test_the_ladder_and_catch_up_stay_in_the_instance(daytime, two):
    sa, pa, sb, pb = two
    with use_instance(sb), session_scope() as s:  # whatever zone B signed up in, 09:00 UTC is a UTC daytime
        pref = preferences_for(s, pb)
        pref.timezone, pref.quiet_start, pref.quiet_end = "UTC", "22:00", "07:00"
    vip_message(sb, pb, "The server is down")
    drain()
    drain(datetime.now(UTC) + timedelta(minutes=30))  # rung 2 and expiry run as B's jobs
    with use_instance(sb), session_scope() as s:
        tdigest.schedule_catchup(s, s.get(Person, pb.id), datetime.now(UTC) + timedelta(minutes=1))
    drain(datetime.now(UTC) + timedelta(minutes=40))
    with session_scope() as s:
        esc = s.scalar(select(Escalation))
        assert esc is not None and esc.state == "expired" and esc.agent_id == sb.agent_id
        # More than one can exist (a message in quiet hours queues its own for when they end): the one
        # this test queued for +1 minute has run, and every one belongs to B.
        catchups = s.scalars(select(Job).where(Job.kind == "triage_catchup")).all()
        assert catchups and {j.agent_id for j in catchups} == {sb.agent_id}
        assert any(j.status == "done" for j in catchups)
        note = s.scalar(select(Event).where(Event.meta["kind"].astext == "triage_catchup"))
        assert note is not None and note.agent_id == sb.agent_id
        assert s.scalars(select(Job).where(Job.agent_id == sa.agent_id, Job.kind.like("%escalation%"))).all() == []


def test_the_stage_is_read_for_the_instance_in_scope(two):
    from superaide.platform.db.models import AgentState
    from superaide.triage.store import current_stage

    sa, pa, sb, pb = two
    with session_scope() as s:
        s.add(AgentState(tenant_id=sb.tenant_id, agent_id=sb.agent_id, key="stage", value={"stage": "adolescent"}))
    with use_instance(sb), session_scope() as s:
        assert current_stage(s) == "adolescent"
    with use_instance(sa), session_scope() as s:
        assert current_stage(s) == "infant"
