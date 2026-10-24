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
from tests.api_support import OTHER, OWNER, hire, signup


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
    assert "stranger_intake" not in s and [r["provider"] for r in s["ladder"]] == ["app", "interrupt", "other"]
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


# --- #117: take a correction back, and the promotion view ----------------------------------------


def test_taking_feedback_back_erases_it(owner_browser):
    did = a_decision(owner_of(OWNER["email"]))
    assert owner_browser.post(f"/app/needs-you/{did}/feedback", json={"feedback": "not_urgent"}).status_code == 200
    r = owner_browser.delete(f"/app/needs-you/{did}/feedback")
    assert r.status_code == 200 and r.json()["changed"] is True and r.json()["item"]["feedback"] is None
    assert owner_browser.delete(f"/app/needs-you/{did}/feedback").json()["changed"] is False
    with session_scope() as s:
        assert s.get(TriageDecision, did).feedback is None


def test_taking_feedback_back_needs_the_owner_and_the_csrf_header(app, browser, owner_browser):
    did = a_decision(owner_of(OWNER["email"]))
    assert browser().delete(f"/app/needs-you/{did}/feedback").status_code == 401
    stranger = browser()
    assert signup(stranger, name="Priya Tan", email=OTHER["email"]).status_code == 200
    assert stranger.delete(f"/app/needs-you/{did}/feedback").status_code == 403
    from fastapi.testclient import TestClient

    bare = TestClient(app, cookies=owner_browser.cookies)
    assert bare.delete(f"/app/needs-you/{did}/feedback").status_code in {400, 403}


def test_the_promotion_view_reads_only_the_owners_own_feedback(owner_browser):
    owner = owner_of(OWNER["email"])
    for i in range(3):
        did = a_decision(owner)
        owner_browser.post(f"/app/needs-you/{did}/feedback", json={"feedback": "useful" if i < 2 else "not_urgent"})
    body = owner_browser.get("/app/triage/promotion").json()
    v = body["verdict"]
    assert (v["useful"], v["not_urgent"], v["n"], v["stage"], v["next_stage"]) == (2, 1, 3, "infant", "child")
    assert v["ready_to_offer"] is False and v["needed_events"] == 30 and "never changes her own stage" in body["note"]


def test_settings_carry_the_knn_shadow_line_only_while_that_flag_is_on(owner_browser, settings, monkeypatch):
    assert owner_browser.get("/app/triage/settings").json()["knn_shadow"] is None
    monkeypatch.setattr(settings, "triage_knn_enabled", True)
    shadow = owner_browser.get("/app/triage/settings").json()["knn_shadow"]
    assert shadow["mode"] == "shadow" and shadow["needs"] == 200 and shadow["eligible"] is False
    assert shadow["text"].startswith("Shadow:")
    assert "knn_shadow" not in owner_browser.put("/app/triage/settings", json={"max_per_day": 3}).json()


# --- #117: the owner's Promote control ---------------------------------------------------------------------------


def _fill_eligible(n: int = 200) -> None:
    from superaide.triage.types import Feedback
    from tests.triage.test_knn_shadow import add
    from tests.triage.test_knn_shadow import world as ctx_world

    with session_scope() as s:
        c = ctx_world(s)
        c.agent_id = owner_of(OWNER["email"]).agent_id
        for i in range(n):
            add(s, c, i, 0.95, Feedback.USEFUL)
        for d in s.scalars(select(TriageDecision).where(TriageDecision.agent_id != owner_of(OWNER["email"]).agent_id)):
            d.agent_id = owner_of(OWNER["email"]).agent_id


def test_promote_is_hidden_while_the_knn_flag_is_off(owner_browser):
    assert owner_browser.post("/app/triage/knn/promote").status_code == 404
    assert owner_browser.post("/app/triage/knn/demote").status_code == 404


def test_promote_is_refused_with_the_numbers_until_eligible_then_recorded(owner_browser, settings, monkeypatch):
    monkeypatch.setattr(settings, "triage_knn_enabled", True)
    card = owner_browser.get("/app/triage/settings").json()["knn_shadow"]
    assert card["can_promote"] is False and card["promoted"] is False and card["z"] == 1.96
    r = owner_browser.post("/app/triage/knn/promote")
    assert r.status_code == 409 and "0 of the 200" in r.json()["detail"]
    _fill_eligible()
    card = owner_browser.get("/app/triage/settings").json()["knn_shadow"]
    assert card["eligible"] is True and card["can_promote"] is True
    done = owner_browser.post("/app/triage/knn/promote")
    assert done.status_code == 200 and done.json()["promoted"] is True and done.json()["can_demote"] is True
    assert owner_browser.get("/app/triage/settings").json()["knn_shadow"]["promoted"] is True
    back = owner_browser.post("/app/triage/knn/demote")
    assert back.status_code == 200 and back.json()["promoted"] is False


def test_promote_and_demote_need_the_owner_and_the_csrf_header(app, browser, owner_browser, settings, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(settings, "triage_knn_enabled", True)
    bare = TestClient(app)
    assert bare.post("/app/triage/knn/promote").status_code in (401, 403)
    owner_browser.headers.pop("X-Requested-With", None)
    assert owner_browser.post("/app/triage/knn/promote").status_code in (400, 403)
    stranger = browser()
    assert signup(stranger, **OTHER).status_code == 200
    stranger.headers["X-Requested-With"] = "superaide"
    assert stranger.post("/app/triage/knn/promote").status_code == 403  # signed in, but hired nobody
    assert stranger.post("/app/triage/knn/demote").status_code == 403


def test_another_owners_promotion_is_theirs_alone(browser, settings, monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)
    monkeypatch.setattr(settings, "triage_knn_enabled", True)
    a, b = browser(), browser()
    assert signup(a).status_code == 200 and hire(a).status_code == 200
    assert signup(b, **OTHER).status_code == 200 and hire(b).status_code == 200
    _fill_eligible()  # owner A's corrections
    assert a.post("/app/triage/knn/promote").status_code == 200
    card = b.get("/app/triage/settings").json()["knn_shadow"]
    assert card["promoted"] is False and card["can_promote"] is False and card["n"] == 0
    assert b.post("/app/triage/knn/promote").status_code == 409
