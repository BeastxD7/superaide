"""ADR 0010 TR7: the owner's triage API. Hidden (404) while TRIAGE_ENABLED is off; owner-only,
CSRF-checked and per instance when it's on."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from superaide.config import get_settings
from superaide.db.models import Context, Event, Person, UserAccount
from superaide.db.session import session_scope
from superaide.main import create_app
from superaide.pipeline import Agent
from superaide.triage.escalate import open_escalation
from superaide.triage.models import TriageDecision
from superaide.triage.types import POLICY_VERSION
from tests.api.conftest import OTHER, OWNER, hire, signup


@pytest.fixture
def app(agent, settings) -> FastAPI:
    on = Agent(
        llm=agent.llm, messengers=agent.messengers, settings=settings.model_copy(update={"triage_enabled": True})
    )
    return create_app(on)


def owner_of(email: str) -> Person:
    with session_scope() as s:
        user = s.scalar(select(UserAccount).where(UserAccount.email_normalized == email))
        assert user is not None
        person = s.scalar(select(Person).where(Person.id == user.person_id))
        assert person is not None
        s.expunge(person)
        return person


def a_decision(owner: Person, decision: str = "interrupt", *, escalate: bool = True, **features) -> uuid.UUID:
    """A recorded decision in the owner's instance, from a colleague, as the triage job leaves it."""
    ids = {"agent_id": owner.agent_id, "tenant_id": owner.tenant_id}
    with session_scope() as s:
        priya = Person(display_name="Priya Tan", relation="colleague", **ids)
        ctx = Context(provider="slack", tenant_scope="T", external_id=uuid.uuid4().hex, kind="dm",
                      audience_class="private_1to1", **ids)  # fmt: skip
        s.add_all([priya, ctx])
        s.flush()
        ev = Event(context_id=ctx.id, direction="in", content="the server is down", resolved_person_id=priya.id,
                   **ids)  # fmt: skip
        s.add(ev)
        s.flush()
        d = TriageDecision(
            inbound_event_id=ev.id, sender_person_id=priya.id, context_id=ctx.id, features=features,
            rule_hits=["R5_vip"], decision=decision, reason="VIP, and it looks urgent", stage_at_time="infant",
            policy_version=POLICY_VERSION,
            classifier={"evidence": ["the server is down"], "risk_flags": []}, **ids,
        )  # fmt: skip
        s.add(d)
        s.flush()
        if escalate:
            esc = open_escalation(s, d, s.get(Person, owner.id), max_step=2, breaks_quiet=False, defer_until=None,
                                  deadline_at=None)  # fmt: skip
            esc.agent_id, esc.tenant_id = owner.agent_id, owner.tenant_id
        return d.id


@pytest.fixture
def owner_browser(browser):
    c = browser()
    assert signup(c).status_code == 200 and hire(c).status_code == 200
    return c


def test_hidden_while_the_flag_is_off(agent, browser):
    off = create_app(agent)
    from fastapi.testclient import TestClient

    c = TestClient(off, headers={"X-Requested-With": "superaide"})
    signup(c), hire(c)
    assert c.get("/app/needs-you").status_code == 404
    assert c.get("/app/triage/settings").status_code == 404
    assert c.post("/app/triage/busy", json={"minutes": 30}).status_code == 404


def test_needs_a_signed_in_owner(browser):
    assert browser().get("/app/needs-you").status_code in {401, 403}


def test_the_inbox_lists_and_an_ack_stops_the_ladder(owner_browser):
    d = a_decision(owner_of(OWNER["email"]))
    held = a_decision(owner_of(OWNER["email"]), "hold", escalate=False)
    body = owner_browser.get("/app/needs-you").json()
    assert [i["id"] for i in body["needs_you"]] == [str(d)]
    assert [i["id"] for i in body["held"]] == [str(held)]
    item = body["needs_you"][0]
    assert item["sender"] == "Priya Tan" and item["quotes"] == ["the server is down"]
    assert item["escalation"]["state"] == "notifying"

    r = owner_browser.post(f"/app/needs-you/{d}/action", json={"verb": "ack", "request_id": "click-1"})
    assert r.status_code == 200 and r.json()["changed"] and r.json()["item"]["escalation"]["state"] == "acked"
    again = owner_browser.post(f"/app/needs-you/{d}/action", json={"verb": "ack", "request_id": "click-1"})
    assert again.json()["changed"] is False

    done = owner_browser.post(f"/app/needs-you/{d}/action", json={"verb": "done"})
    assert done.json()["item"]["escalation"]["state"] == "resolved"
    assert owner_browser.get("/app/needs-you").json()["counts"]["needs_you"] == 0


def test_not_urgent_writes_feedback(owner_browser):
    d = a_decision(owner_of(OWNER["email"]))
    r = owner_browser.post(f"/app/needs-you/{d}/action", json={"verb": "not_urgent"})
    assert r.json()["item"]["feedback"] == "not_urgent"
    assert r.json()["item"]["escalation"]["resolution"] == "dismissed"


def test_snooze_re_enqueues(owner_browser):
    from superaide.db.models import Job

    d = a_decision(owner_of(OWNER["email"]))
    r = owner_browser.post(f"/app/needs-you/{d}/action", json={"verb": "snooze_2h"})
    esc = r.json()["item"]["escalation"]
    assert esc["state"] == "deferred" and esc["cycle"] == 1
    with session_scope() as s:
        job = s.scalar(select(Job).where(Job.dedupe_key == f"escalation:{esc['id']}:step:0:c1"))
        assert job is not None and job.status == "queued"


def test_an_unescalated_item_is_closed_by_feedback(owner_browser):
    d = a_decision(owner_of(OWNER["email"]), "verify", escalate=False)
    assert owner_browser.get("/app/needs-you").json()["counts"]["needs_you"] == 1
    assert owner_browser.post(f"/app/needs-you/{d}/action", json={"verb": "snooze_30m"}).status_code == 409
    owner_browser.post(f"/app/needs-you/{d}/action", json={"verb": "done"})
    assert owner_browser.get("/app/needs-you").json()["counts"]["needs_you"] == 0


def test_should_have_told_me(owner_browser):
    held = a_decision(owner_of(OWNER["email"]), "hold", escalate=False)
    r = owner_browser.post(f"/app/needs-you/{held}/feedback", json={"feedback": "should_have_told"})
    assert r.status_code == 200 and r.json()["item"]["feedback"] == "should_have_told"
    assert owner_browser.post(f"/app/needs-you/{held}/feedback", json={"feedback": "meh"}).status_code == 422


def test_quotes_are_never_shown_for_payment_items(owner_browser):
    d = a_decision(owner_of(OWNER["email"]), "verify", escalate=False)
    with session_scope() as s:
        row = s.get(TriageDecision, d)
        row.classifier = {"evidence": ["new IBAN"], "risk_flags": ["payment"]}
    item = owner_browser.get("/app/needs-you").json()["needs_you"][0]
    assert item["quotes"] == []


def test_csrf_is_required_for_changes(owner_browser, browser):
    d = a_decision(owner_of(OWNER["email"]))
    owner_browser.headers.pop("X-Requested-With")
    assert owner_browser.post(f"/app/needs-you/{d}/action", json={"verb": "ack"}).status_code == 403
    assert owner_browser.put("/app/triage/settings", json={"max_per_day": 3}).status_code == 403


def test_settings_defaults_changes_and_limits(owner_browser):
    s = owner_browser.get("/app/triage/settings").json()
    assert (s["max_per_day"], s["max_per_hour"], s["theta_interrupt"], s["away_visibility"]) == (
        6,
        2,
        0.6,
        "colleagues",
    )
    assert s["stranger_intake"] is False and [r["provider"] for r in s["ladder"]] == ["app", "interrupt", "other"]
    r = owner_browser.put("/app/triage/settings", json={"max_per_day": 3, "silenced_topics": [" golf ", ""]})
    assert r.json()["max_per_day"] == 3 and r.json()["silenced_topics"] == ["golf"] and r.json()["max_per_hour"] == 2
    assert owner_browser.put("/app/triage/settings", json={"theta_interrupt": 0.1}).status_code == 422
    assert owner_browser.put("/app/triage/settings", json={"ladder": []}).status_code == 422


def test_away_and_busy(owner_browser):
    r = owner_browser.post("/app/triage/away", json={"until": "2030-01-01T09:00:00+00:00", "visibility": "nobody"})
    assert r.json()["away_until"].startswith("2030-01-01") and r.json()["away_visibility"] == "nobody"
    assert owner_browser.post("/app/triage/away", json={"until": "2030-01-01T09:00:00"}).status_code == 422
    assert owner_browser.post("/app/triage/away", json={"until": None}).json()["away_until"] is None
    assert owner_browser.post("/app/triage/busy", json={"minutes": 90}).json()["busy_until"] is not None
    assert owner_browser.post("/app/triage/busy", json={"minutes": 0}).json()["busy_until"] is None


def test_another_owner_never_sees_or_touches_the_items(browser, monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)
    a, b = browser(), browser()
    assert signup(a).status_code == 200 and hire(a).status_code == 200
    assert signup(b, **OTHER).status_code == 200 and hire(b).status_code == 200
    d = a_decision(owner_of(OWNER["email"]))
    assert owner_of(OTHER["email"]).agent_id != owner_of(OWNER["email"]).agent_id
    assert b.get("/app/needs-you").json()["counts"]["needs_you"] == 0
    assert b.post(f"/app/needs-you/{d}/action", json={"verb": "ack"}).status_code == 404
    assert b.post(f"/app/needs-you/{d}/feedback", json={"feedback": "useful"}).status_code == 404
    assert a.get("/app/needs-you").json()["counts"]["needs_you"] == 1
