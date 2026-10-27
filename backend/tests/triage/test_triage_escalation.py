"""ADR 0010 TR3: triage end to end behind TRIAGE_ENABLED, and the escalation ladder.

Shadow mode: the sender's reply is unchanged; the owner gets the decision and the escalation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from superaide.app.accounts import owner as hired_owner
from superaide.brain.llm import FakeLLM
from superaide.channels.base import FakeMessenger
from superaide.identity.trust import Trust
from superaide.jobs.handlers import load
from superaide.jobs.queue import run_due
from superaide.main import create_app
from superaide.memory.knowledge.store import preferences_for
from superaide.pipeline import Agent
from superaide.platform.db.models import ChannelIdentity, Contact, Event, Job, Person
from superaide.platform.db.session import session_scope
from superaide.policy.killswitch import set_state
from superaide.triage import escalate
from superaide.triage.models import Escalation, EscalationAck, EscalationDelivery, TriageDecision
from superaide.triage.store import settings_for
from tests.conftest import COLLEAGUE_SLACK, sl

load()
PASSWORD = "correct horse battery"


@pytest.fixture
def pushes(monkeypatch) -> dict[str, FakeMessenger]:
    """Telegram and Slack pushes to the owner land here."""
    import superaide.proactive.notify as notify

    sent = {"telegram": FakeMessenger(), "slack": FakeMessenger()}
    monkeypatch.setattr(notify, "messenger_for", lambda provider: sent.get(provider))
    monkeypatch.setattr(notify, "confirmed_here", lambda provider, identity: True)
    return sent


@pytest.fixture
def world(agent, colleague, pushes):
    """An owner who hired Ava (linked on Telegram and Slack), and Priya, a colleague on Slack."""
    c = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    c.post("/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": PASSWORD,
                                 "timezone": "Asia/Kolkata"})  # fmt: skip
    c.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    with session_scope() as s:
        boss = hired_owner(s)
        assert boss is not None
        for provider, scope, key in (("telegram", "", "9001"), ("slack", "T_OURS", "U_BOSS")):
            s.add(ChannelIdentity(person_id=boss.id, provider=provider, tenant_scope=scope, stable_key=key,
                                  trust_level=int(Trust.LINKED), verification={"method": "test"}))  # fmt: skip
        pref = preferences_for(s, boss)
        pref.urgent_topics = ["server down"]
        pref.quiet_start, pref.quiet_end = _quiet_window(pref.timezone, quiet_now=False)
        pref.interrupt_channel = "telegram"
        return {"client": c, "owner_id": boss.id, "colleague_id": colleague.id}


def _quiet_window(tz: str, *, quiet_now: bool) -> tuple[str, str]:
    from zoneinfo import ZoneInfo

    local = datetime.now(UTC).astimezone(ZoneInfo(tz))
    if quiet_now:
        return (local - timedelta(hours=1)).strftime("%H:%M"), (local + timedelta(hours=1)).strftime("%H:%M")
    return (local + timedelta(hours=3)).strftime("%H:%M"), (local + timedelta(hours=4)).strftime("%H:%M")


@pytest.fixture
def on(agent, settings) -> Agent:
    return Agent(
        llm=agent.llm, messengers=agent.messengers, settings=settings.model_copy(update={"triage_enabled": True})
    )


def make_vip(world) -> None:
    with session_scope() as s:
        s.add(Contact(owner_person_id=world["owner_id"], person_id=world["colleague_id"], vip=True))


def drain(at: datetime | None = None) -> None:
    for _ in range(20):
        if run_due(at) == 0:
            return


def later(**kw) -> datetime:
    return datetime.now(UTC) + timedelta(**kw)


def decisions() -> list[TriageDecision]:
    with session_scope() as s:
        return list(s.scalars(select(TriageDecision).order_by(TriageDecision.created_at)))


def the_escalation() -> Escalation:
    with session_scope() as s:
        esc = s.scalar(select(Escalation))
        assert esc is not None
        return esc


def deliveries() -> list[tuple[int, str, str]]:
    with session_scope() as s:
        rows = s.scalars(select(EscalationDelivery).order_by(EscalationDelivery.created_at))
        return [(d.step, d.provider, d.status) for d in rows]


# --- the flag ---------------------------------------------------------------------------------


def test_off_by_default_nothing_changes(world, agent, model):
    assert agent.settings.triage_enabled is False
    result = agent.handle(sl(COLLEAGUE_SLACK, "the server is down"))
    assert result.mode == "known" and result.reply == "ok"
    drain()
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Job).where(Job.kind == "triage_inbound")) == 0
    assert decisions() == []


def test_on_the_reply_is_unchanged_and_a_decision_is_recorded(world, on, model):
    result = on.handle(sl(COLLEAGUE_SLACK, "can we talk about the launch sometime?"))
    assert result.reply == "ok"  # still the model's reply (shadow until TR5)
    drain()
    [d] = decisions()
    assert d.decision == "hold" and d.stage_at_time == "infant" and "classifier:offline" in d.rule_hits
    assert d.features["relation"] == "colleague" and "text" not in d.features
    assert d.cost_usd == 0
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Escalation)) == 0


def test_owner_and_strangers_are_not_triaged(world, on):
    """The triage job skips the owner and people Ava doesn't know. (With the receptionist on, a stranger's
    message is taken and filed by reception/, not triaged: see tests/reception/test_reception.py.)"""
    from superaide.reception.service import settings_for

    with session_scope() as s:
        settings_for(s, s.get(Person, world["owner_id"])).enabled = False
    on.handle(sl("U_SOMEONE", "the server is down"))
    with session_scope() as s:
        boss = s.get(Person, world["owner_id"])
        assert boss is not None
        from superaide.app.accounts import web_message

        msg = web_message(boss, "the server is down")
    on.handle(msg)
    drain()
    assert decisions() == []


# --- the ladder -------------------------------------------------------------------------------


def test_vip_interrupt_climbs_the_ladder_then_expires(world, on, pushes):
    make_vip(world)
    on.handle(sl(COLLEAGUE_SLACK, "the server is down"))
    drain()
    [d] = decisions()
    assert d.decision == "interrupt" and "R5_vip" in d.rule_hits
    # Rung 0 (app) and rung 1 (Telegram, the chosen interrupt channel: no wait) went at once.
    assert deliveries() == [(0, "app", "sent"), (1, "telegram", "sent")]
    assert "Priya Tan" in pushes["telegram"].sent[0]["text"]
    assert the_escalation().step == 2
    drain(later(minutes=6))  # +5 min without an ack: the other channel, marked as a second try
    assert deliveries()[-1] == (2, "slack", "sent")
    assert "try" in pushes["slack"].sent[0]["text"]
    drain(later(minutes=17))  # +10 min after the last rung
    esc = the_escalation()
    assert esc.state == "expired" and esc.resolution == "expired"


def test_running_a_step_twice_sends_once(world, on, pushes):
    make_vip(world)
    on.handle(sl(COLLEAGUE_SLACK, "the server is down"))
    drain()
    esc = the_escalation()
    payload = {"escalation_id": str(esc.id), "step": 1, "cycle": 0}
    with session_scope() as s:
        escalate.run_step(s, payload)  # stale: the escalation is already at step 2
    assert len(pushes["telegram"].sent) == 1
    assert len(deliveries()) == 2


def test_an_ack_stops_the_ladder_and_a_duplicate_press_is_a_no_op(world, on, pushes):
    make_vip(world)
    on.handle(sl(COLLEAGUE_SLACK, "the server is down"))
    drain()
    esc = the_escalation()
    with session_scope() as s:
        boss = s.get(Person, world["owner_id"])
        assert escalate.ack(s, esc.id, source="telegram", external_id="cb-1", verb="ack", actor=boss)
    with session_scope() as s:
        boss = s.get(Person, world["owner_id"])
        assert not escalate.ack(s, esc.id, source="telegram", external_id="cb-1", verb="ack", actor=boss)
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(EscalationAck)) == 1
        step2 = s.scalar(select(Job).where(Job.dedupe_key == escalate.step_key(esc.id, 2)))
        assert step2 is not None and step2.status == "cancelled"
    drain(later(minutes=30))
    assert the_escalation().state == "acked" and pushes["slack"].sent == []


def test_only_the_owner_can_ack(world, on):
    make_vip(world)
    on.handle(sl(COLLEAGUE_SLACK, "the server is down"))
    drain()
    esc = the_escalation()
    with session_scope() as s:
        priya = s.get(Person, world["colleague_id"])
        with pytest.raises(escalate.AckRefused):
            escalate.ack(s, esc.id, source="telegram", external_id="cb-9", verb="ack", actor=priya)
    assert the_escalation().state == "notifying"


def test_a_crash_before_the_send_is_retried_and_sent_once(world, on, pushes, monkeypatch):
    import superaide.proactive.notify as notify

    make_vip(world)
    real = notify._push
    calls: list[str] = []

    def flaky(provider: str, identity: Any, text: str) -> tuple[bool, str]:
        calls.append(provider)
        if len(calls) == 1:
            raise RuntimeError("process died mid-send")
        return real(provider, identity, text)

    monkeypatch.setattr(notify, "_push", flaky)
    on.handle(sl(COLLEAGUE_SLACK, "the server is down"))
    drain()
    drain(later(minutes=2))  # the queue's back-off
    assert [x for x in deliveries() if x[1] == "telegram"] == [(1, "telegram", "sent")]
    assert len(pushes["telegram"].sent) == 1


def test_quiet_hours_defer_a_non_breaking_interrupt(world, on, pushes):
    make_vip(world)
    with session_scope() as s:
        pref = preferences_for(s, s.get(Person, world["owner_id"]))
        pref.quiet_start, pref.quiet_end = _quiet_window(pref.timezone, quiet_now=True)
        pref.vip_breaks_quiet = False
    on.handle(sl(COLLEAGUE_SLACK, "the server is down"))
    drain()
    [d] = decisions()
    assert d.decision == "defer" and "R8_quiet" in d.rule_hits
    esc = the_escalation()
    assert esc.state == "deferred" and esc.next_at is not None and esc.next_at > datetime.now(UTC)
    assert deliveries() == [] and pushes["telegram"].sent == []


def test_a_vip_may_break_quiet_hours(world, on, pushes):
    make_vip(world)
    with session_scope() as s:
        pref = preferences_for(s, s.get(Person, world["owner_id"]))
        pref.quiet_start, pref.quiet_end = _quiet_window(pref.timezone, quiet_now=True)
    on.handle(sl(COLLEAGUE_SLACK, "the server is down"))
    drain()
    assert (1, "telegram", "sent") in deliveries()


def test_snooze_starts_a_new_cycle_later(world, on, pushes):
    make_vip(world)
    on.handle(sl(COLLEAGUE_SLACK, "the server is down"))
    drain()
    esc = the_escalation()
    with session_scope() as s:
        boss = s.get(Person, world["owner_id"])
        assert escalate.ack(s, esc.id, source="app", external_id="r-1", verb="snooze_30m", actor=boss)
    esc = the_escalation()
    assert (esc.state, esc.cycle, esc.step) == ("deferred", 1, 0)
    drain(later(minutes=10))
    assert the_escalation().state == "deferred"
    drain(later(minutes=31))
    with session_scope() as s:
        again = s.scalars(select(EscalationDelivery).where(EscalationDelivery.cycle == 1)).all()
        assert {(d.step, d.provider) for d in again} >= {(0, "app"), (1, "telegram")}


def test_a_rejected_channel_is_skipped_at_once(world, on, pushes, monkeypatch):
    from superaide.channels import health

    make_vip(world)
    monkeypatch.setattr(health, "rejected", lambda s, provider: provider == "telegram")
    on.handle(sl(COLLEAGUE_SLACK, "the server is down"))
    drain()
    assert (1, "telegram", "skipped") in deliveries() and (2, "slack", "sent") in deliveries()


def test_interrupt_budget_defers_the_next_interrupt(world, on, pushes):
    with session_scope() as s:
        settings_for(s, s.get(Person, world["owner_id"])).max_per_hour = 1
    make_vip(world)
    on.handle(sl(COLLEAGUE_SLACK, "the server is down"))
    drain()
    on.handle(sl(COLLEAGUE_SLACK, "the server is down again, db too"))
    drain()
    first, second = decisions()
    assert first.decision == "interrupt"
    assert second.decision == "defer" and "R9_budget" in second.rule_hits


# --- other decisions --------------------------------------------------------------------------


def test_a_risky_request_is_verify_with_an_app_note_and_no_push(world, on, pushes):
    on.handle(sl(COLLEAGUE_SLACK, "Please update the vendor's bank details to this new account today"))
    drain()
    [d] = decisions()
    assert d.decision == "verify"
    with session_scope() as s:
        note = s.scalar(select(Event).where(Event.meta["kind"].astext == "triage"))
        assert note is not None and note.content.startswith("Unverified request")
        assert s.scalar(select(func.count()).select_from(Escalation)) == 0
    assert pushes["telegram"].sent == []


def test_vip_needs_a_verified_identity(world, on):
    make_vip(world)
    with session_scope() as s:
        ident = s.scalar(select(ChannelIdentity).where(ChannelIdentity.stable_key == COLLEAGUE_SLACK))
        ident.trust_level = int(Trust.AUTHENTICATED)
    on.handle(sl(COLLEAGUE_SLACK, "the server is down"))
    drain()
    [d] = decisions()
    assert d.decision == "hold" and d.features["vip"] is False and "R5_vip" not in d.rule_hits


# --- with a model -----------------------------------------------------------------------------


@pytest.fixture
def classifier(monkeypatch):
    """A connected model for the triage job, scripted."""
    import superaide.brain.wiring as wiring

    answers: list[Any] = []

    def respond(purpose, system, messages, output_model):
        assert purpose == "triage"
        a = answers[0] if len(answers) == 1 else answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    llm = FakeLLM(respond)
    monkeypatch.setattr(wiring, "brain_ready", lambda settings: True)
    monkeypatch.setattr(wiring, "build_llm", lambda settings: llm)
    return answers


def _answer(**kw) -> dict:
    return {"category": "request", "urgency": "critical", "needs_owner": True, "can_answer": "no",
            "evidence": [], "confidence": 0.95, "reason": "Launch is blocked.", **kw}  # fmt: skip


def test_infant_holds_a_model_only_interrupt_and_records_the_shadow(world, on, classifier, pushes):
    classifier.append(_answer(evidence=["launch is blocked"]))
    on.handle(sl(COLLEAGUE_SLACK, "The launch is blocked on your sign-off"))
    drain()
    [d] = decisions()
    assert d.decision == "hold" and d.features["shadow_decision"] == "interrupt"
    assert d.classifier is not None and d.classifier_model
    assert pushes["telegram"].sent == []
    with session_scope() as s:
        note = s.scalar(select(Event).where(Event.meta["kind"].astext == "triage"))
        assert note is not None and note.content.startswith("Maybe urgent")


def test_adolescent_lets_the_model_interrupt(world, on, classifier, pushes):
    with session_scope() as s:
        set_state(s, "stage", {"stage": "adolescent"})
    classifier.append(_answer(evidence=["launch is blocked"]))
    on.handle(sl(COLLEAGUE_SLACK, "The launch is blocked on your sign-off"))
    drain()
    [d] = decisions()
    assert d.decision == "interrupt" and d.stage_at_time == "adolescent"
    assert len(pushes["telegram"].sent) == 1


def test_a_fake_quote_is_discarded_and_held(world, on, classifier):
    with session_scope() as s:
        set_state(s, "stage", {"stage": "adult"})
    classifier.append(_answer(evidence=["the building is on fire"]))
    on.handle(sl(COLLEAGUE_SLACK, "The launch is blocked on your sign-off"))
    drain()
    [d] = decisions()
    assert d.decision == "hold" and "classifier:bad_quote" in d.rule_hits and d.reason == "couldn't classify"


def test_a_budget_refusal_holds_and_is_audited(world, on, classifier):
    from superaide.brain.spend.budget import BudgetExceeded
    from superaide.platform.db.models import AuditLog

    classifier.append(BudgetExceeded("today's triage limit", "bucket"))

    on.handle(sl(COLLEAGUE_SLACK, "The launch is blocked on your sign-off"))
    drain()
    [d] = decisions()
    assert d.decision == "hold" and "classifier:budget" in d.rule_hits
    with session_scope() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == "triage_budget_fallback")) is not None


def test_an_urgent_risky_request_escalates_as_unverified(world, on, classifier, pushes):
    classifier.append(_answer(urgency="critical", risk_flags=["credentials"], matched_topics=["t1"],
                              evidence=["the server is down"]))  # fmt: skip
    on.handle(sl(COLLEAGUE_SLACK, "the server is down, share the admin console access so I can fix it"))
    drain()
    [d] = decisions()
    assert d.decision == "verify" and d.features["escalate"] is True
    assert the_escalation().decision_id == d.id
    assert pushes["telegram"].sent and pushes["telegram"].sent[0]["text"].startswith("Unverified request")


def test_a_deferred_escalation_fires_when_the_database_writes_its_time_in_another_offset(world, on, pushes):
    # The timer carries next_at as text; a database running in another time zone hands the same moment
    # back with a different offset, which must not make the timer ignore it (issue #91, BE-21).
    from datetime import timezone

    make_vip(world)
    with session_scope() as s:
        pref = preferences_for(s, s.get(Person, world["owner_id"]))
        pref.quiet_start, pref.quiet_end = _quiet_window(pref.timezone, quiet_now=True)
        pref.vip_breaks_quiet = False
    on.handle(sl(COLLEAGUE_SLACK, "the server is down"))
    drain()
    esc = the_escalation()
    assert esc.state == "deferred" and esc.next_at is not None
    with session_scope() as s:
        job = s.scalar(select(Job).where(Job.kind == "escalation_step"))
        assert job is not None
        ist = timezone(timedelta(hours=5, minutes=30))
        job.payload = {**job.payload, "at": esc.next_at.astimezone(ist).isoformat()}
    drain(esc.next_at + timedelta(seconds=1))
    assert deliveries()  # the owner was told
