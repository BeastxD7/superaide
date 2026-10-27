"""Gmail inbox triage: polling with history.list per instance, verified senders mapped to contacts,
newsletters held, strangers never sent to a model, the Needs-you inbox and the digest's email section.
Gmail is a fake (tests/integrations/gmail_fake.py)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.identity.models import ChannelIdentity, Person
from superaide.identity.trust import Trust
from superaide.integrations.gmail import client as api
from superaide.integrations.gmail import inbox as gmail_inbox
from superaide.integrations.gmail import skill as gmail
from superaide.jobs.models import Job
from superaide.jobs.queue import run_due
from superaide.memory.models import Contact, Event
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import InstanceScope, use_instance
from superaide.triage import digest as triage_digest
from superaide.triage.models import TriageDecision
from tests.integrations.gmail_fake import connect, gmail_fake_fixture, mail, owner_client_fixture  # noqa: F401  (fixtures)


@pytest.fixture
def flags(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "triage_enabled", True)
    monkeypatch.setattr(s, "gmail_triage_enabled", True)
    return s


def contact(email: str, *, name: str = "Daniel Kim", vip: bool = False) -> uuid.UUID:
    with session_scope() as s:
        owner = hired_owner(s)
        p = Person(display_name=name, relation="contact")
        s.add(p)
        s.flush()
        s.add(Contact(owner_person_id=owner.id, person_id=p.id, relationship="team", vip=vip))
        s.add(ChannelIdentity(person_id=p.id, provider="email", stable_key=email, trust_level=int(Trust.LINKED)))
        return p.id


def poll() -> int:
    with session_scope() as s:
        return gmail_inbox.poll(s)


def decisions() -> list[TriageDecision]:
    with session_scope() as s:
        rows = list(s.scalars(select(TriageDecision).order_by(TriageDecision.created_at)))
        s.expunge_all()
        return rows


def test_the_first_poll_only_marks_where_the_inbox_is_then_new_mail_is_triaged(owner_client, gmail_fake, flags):
    gmail_fake.add(mail("old1"))
    connect()
    assert poll() == 0 and decisions() == []  # older mail is never triaged
    gmail_fake.add(mail("n1", subject="Quarterly numbers", body="Here are the numbers you asked for."))
    assert poll() == 1
    [d] = decisions()
    assert d.decision == "hold" and d.features["provider"] == "email"
    assert poll() == 0 and len(decisions()) == 1  # each email once
    with session_scope() as s:
        e = s.get(Event, d.inbound_event_id)
        assert e.audience_person_ids == [] and e.meta["untrusted"] and e.meta["subject"] == "Quarterly numbers"


def test_strangers_and_newsletters_never_reach_a_model(owner_client, gmail_fake, flags, monkeypatch):
    class NoModel:
        def __getattr__(self, name):
            pytest.fail("no model call for these")

    connect()
    poll()
    gmail_fake.add(mail("s1", sender="Eve <eve@unknown.example>", body="Can we talk about a partnership?"))
    gmail_fake.add(mail("s2", sender="Shop <news@shop.example>", extra={"List-Unsubscribe": "<mailto:x@y>"},
                        subject="50% off"))  # fmt: skip
    with session_scope() as s:
        assert gmail_inbox.poll(s, llm=NoModel(), model="m", ready=True) == 2
    stranger, newsletter = decisions()
    assert stranger.decision == "hold" and newsletter.decision == "hold" and "R4_automated" in newsletter.rule_hits


def test_a_verified_contact_is_known_and_an_unverified_from_line_is_not(owner_client, gmail_fake, flags):
    daniel = contact("daniel@acme.com", vip=True)
    connect()
    poll()
    gmail_fake.add(mail("v1"))
    gmail_fake.add(mail("v2", dmarc=False, body="It's Daniel, from a new laptop."))
    poll()
    verified, spoofed = decisions()
    assert verified.sender_person_id == daniel and verified.features["trust"] == int(Trust.LINKED)
    assert verified.features["vip"] is True
    assert spoofed.sender_person_id == daniel and spoofed.features["trust"] == int(Trust.HINT)
    assert spoofed.features["vip"] is False  # an unchecked From line never makes someone a VIP


def test_an_urgent_email_from_a_verified_vip_interrupts(owner_client, gmail_fake, flags):
    with session_scope() as s:
        from superaide.memory.knowledge.store import preferences_for

        prefs = preferences_for(s, hired_owner(s))
        prefs.urgent_topics = ["server down"]
        prefs.quiet_start, prefs.quiet_end = "23:59", "00:00"
    contact("daniel@acme.com", vip=True)
    connect()
    poll()
    gmail_fake.add(mail("u1", subject="Server down", body="The server is down and customers can't log in."))
    poll()
    [d] = decisions()
    assert d.decision in {"interrupt", "defer"} and "R5_vip" in d.rule_hits


def test_payment_and_code_mail_is_an_unverified_request_and_the_digest_never_quotes_it(owner_client, gmail_fake,
                                                                                      flags):  # fmt: skip
    contact("daniel@acme.com")
    connect()
    poll()
    gmail_fake.add(mail("p1", subject="New bank details", body="Please update our bank account details to IBAN X."))
    for i in range(3):
        gmail_fake.add(mail(f"nl{i}", sender="News <hello@shop.example>", extra={"Precedence": "bulk"}))
    gmail_fake.add(mail("q1", sender="Eve <eve@unknown.example>", subject="Coffee?", body="Coffee next week?"))
    poll()
    with session_scope() as s:
        owner = hired_owner(s)
        lines = triage_digest.digest_lines(s, owner, datetime.now(UTC) + timedelta(minutes=1))
    text = "\n".join(lines)
    assert "**Email:** 1 email needs you; 0 handled; 3 newsletters held; 1 other held" in text
    assert "New bank details" not in text and "IBAN" not in text and "not quoted" in text
    assert "Coffee?" not in text  # held, not in the top items
    # The generic sections don't count email twice.
    assert "Messages taken" not in text and "FYI" not in text


def test_the_needs_you_inbox_says_it_is_an_email_and_hides_risky_subjects(owner_client, gmail_fake, flags):
    contact("daniel@acme.com")
    connect()
    poll()
    gmail_fake.add(mail("p1", subject="Password reset code", body="Your verification code is 123456"))
    poll()
    data = owner_client.get("/app/needs-you").json()
    [item] = data["needs_you"]
    assert item["source"] == "email" and item["sender"] == "Daniel Kim" and item["subject"] is None
    assert "123456" not in str(data)


def test_polling_is_per_instance_and_off_by_default(gmail_fake, monkeypatch):
    a = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
    b = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
    with use_instance(a):
        connect()
        with session_scope() as s:
            gmail_inbox.ensure_scheduled(s)
            assert s.scalar(select(Job).where(Job.kind == "gmail_poll")) is None  # flags off
    s_ = get_settings()
    monkeypatch.setattr(s_, "triage_enabled", True)
    monkeypatch.setattr(s_, "gmail_triage_enabled", True)
    with use_instance(a), session_scope() as s:
        gmail_inbox.ensure_scheduled(s)
    with use_instance(b), session_scope() as s:
        gmail_inbox.ensure_scheduled(s)  # b never connected Gmail
    with session_scope() as s:
        from superaide.platform.tenancy.context import all_instances

        with all_instances():
            jobs = list(s.scalars(select(Job).where(Job.kind == "gmail_poll")))
        assert [j.agent_id for j in jobs] == [a.agent_id]
    with use_instance(a), session_scope() as s:
        assert gmail.can_read(s)
    with use_instance(b), session_scope() as s:
        assert not gmail.can_read(s) and gmail_inbox.poll(s) == 0


def test_the_poll_job_reschedules_itself_and_survives_gmail_being_down(owner_client, gmail_fake, flags):
    connect()
    with session_scope() as s:
        gmail_inbox.ensure_scheduled(s)
    gmail_fake.fail = True
    later = datetime.now(UTC) + timedelta(minutes=get_settings().gmail_poll_minutes + 1)
    assert run_due(later) >= 1
    with session_scope() as s:
        jobs = list(s.scalars(select(Job).where(Job.kind == "gmail_poll").order_by(Job.run_at)))
        assert jobs[0].status == "done" and jobs[-1].status == "queued" and len(jobs) == 2


def test_an_expired_history_starts_again_from_now(owner_client, gmail_fake, flags):
    connect()
    poll()
    gmail_fake.oldest_history = 10**9  # Google forgot our place
    gmail_fake.add(mail("lost"))
    assert poll() == 0 and decisions() == []
    gmail_fake.oldest_history = 0
    gmail_fake.add(mail("next"))
    assert poll() == 1


def test_an_email_never_reaches_recall(owner_client, gmail_fake, flags):
    from superaide.memory.audience import Audience
    from superaide.memory.recall.retrieval import recall

    connect()
    poll()
    gmail_fake.add(mail("r1", sender="Eve <eve@unknown.example>", subject="Bluebird", body="Bluebird plans inside"))
    poll()
    with session_scope() as s:
        owner = hired_owner(s)
        aud = Audience(person_ids={owner.id}, has_unknown=False, all_internal=True, min_trust=2)
        assert all("Bluebird" not in src.text for src in recall(s, aud, "Bluebird plans"))
    assert api.READONLY


def test_only_a_verified_known_sender_is_classified_by_the_model(owner_client, gmail_fake, flags, monkeypatch):
    from superaide.triage import classify

    seen: list[str] = []
    real = classify.classify

    def spy(llm, *, model, f, earlier=None, ready=True, **kwargs):
        seen.append(f"{f.sender_name}:{ready}")
        return real(llm, model=model, f=f, earlier=earlier, ready=False, **kwargs)

    monkeypatch.setattr(classify, "classify", spy)
    contact("daniel@acme.com")
    connect()
    poll()
    gmail_fake.add(mail("k1", body="Can you look at the draft before Friday?"))
    gmail_fake.add(mail("k2", dmarc=False, body="Can you look at the draft before Friday?"))
    with session_scope() as s:
        gmail_inbox.poll(s, llm=object(), model="m", ready=True)
    assert seen == ["Daniel Kim:True", "Daniel Kim:False"]  # the unverified copy gets rules only


def test_a_poll_that_blows_up_still_queues_the_next_one(owner_client, gmail_fake, flags, monkeypatch):
    connect()
    with session_scope() as s:
        gmail_inbox.ensure_scheduled(s)
    monkeypatch.setattr(gmail_inbox, "poll", lambda *a, **k: 1 / 0)
    assert run_due(datetime.now(UTC) + timedelta(minutes=get_settings().gmail_poll_minutes + 1)) >= 1
    with session_scope() as s:
        jobs = list(s.scalars(select(Job).where(Job.kind == "gmail_poll")))
        assert sorted(j.status for j in jobs) == ["done", "queued"]
