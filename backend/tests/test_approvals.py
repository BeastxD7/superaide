"""Approvals: Ava asks, only the owner decides, once; trust tiers are enforced."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.brain.turn import RequestedAction
from superaide.db.models import Approval, AuditLog
from superaide.db.session import session_scope
from superaide.main import create_app
from tests.conftest import MANAGER_TG, tg

PASSWORD = "correct horse battery"


@pytest.fixture
def browser(agent):
    app = create_app(agent)

    def make() -> TestClient:
        return TestClient(app, headers={"X-Requested-With": "superaide"})

    return make


def hired(browser, email="s@example.com", name="Shashank Rao") -> TestClient:
    c = browser()
    assert c.post("/auth/signup", json={"name": name, "email": email, "password": PASSWORD}).status_code == 200
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c


def ask_for(owner: TestClient, model, kind: str, summary: str) -> dict:
    model.next_action = RequestedAction(kind=kind, summary=summary)
    assert owner.post("/app/chat", json={"text": summary}).status_code == 200
    model.next_action = RequestedAction(kind="none", summary="")
    return owner.get("/app/approvals").json()[0]


def test_the_owner_sees_a_request_and_approves_it_once(browser, model):
    owner = hired(browser)
    pending = ask_for(owner, model, "publish_post", "Publish the launch post")
    assert pending["status"] == "pending" and pending["summary"] == "Publish the launch post"
    assert pending["requested_by"] == "Shashank Rao"

    r = owner.post(f"/app/approvals/{pending['id']}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    # Nothing can carry it out yet, and Ava says so instead of pretending.
    assert "Nothing can carry this out yet" in r.json()["result"]
    assert owner.get("/app/approvals").json()[0]["status"] == "approved"

    again = owner.post(f"/app/approvals/{pending['id']}/reject")
    assert again.status_code == 409 and "already approved" in again.json()["detail"]
    with session_scope() as s:
        actions = [a.action for a in s.scalars(select(AuditLog)).all()]
        assert "approval_requested" in actions and "approval_approved" in actions


def test_a_rejected_request_is_recorded_and_never_runs(browser, model):
    owner = hired(browser)
    pending = ask_for(owner, model, "message_someone_else", "Tell Priya I'm running late")
    r = owner.post(f"/app/approvals/{pending['id']}/reject")
    assert r.status_code == 200 and r.json() == {"status": "rejected", "result": None}
    with session_scope() as s:
        approval = s.scalar(select(Approval))
        assert approval is not None and approval.decided_by == "Shashank Rao" and approval.decided_at is not None


def test_only_the_owner_can_see_or_decide_approvals(browser, model):
    owner = hired(browser)
    pending = ask_for(owner, model, "publish_post", "Publish it")
    stranger = browser()
    stranger.post("/auth/signup", json={"name": "Priya Nair", "email": "p@example.com", "password": PASSWORD})
    assert stranger.get("/app/approvals").status_code == 403
    assert stranger.post(f"/app/approvals/{pending['id']}/approve").status_code == 403
    signed_out = browser()
    assert signed_out.post(f"/app/approvals/{pending['id']}/approve").status_code == 401
    assert owner.get("/app/approvals").json()[0]["status"] == "pending"


def test_unknown_approvals_and_verdicts_are_not_found(browser, model):
    owner = hired(browser)
    pending = ask_for(owner, model, "publish_post", "Publish it")
    assert owner.post(f"/app/approvals/{pending['id']}/maybe").status_code == 404
    assert owner.post("/app/approvals/00000000-0000-4000-8000-00000000abcd/approve").status_code == 404


def test_actions_that_need_a_stepped_up_identity_are_refused_in_chat(agent, manager, model):
    # The manager is linked (tier 2); deleting memory or granting access needs tier 4.
    for kind in ("delete_memory", "grant_access"):
        model.next_action = RequestedAction(kind=kind, summary="do it")
        result = agent.handle(tg(MANAGER_TG, "please do it"))
        assert f"action:{kind}:refused" in result.notes
    with session_scope() as s:
        assert s.scalar(select(Approval)) is None
        refusals = [a.detail for a in s.scalars(select(AuditLog).where(AuditLog.action == "action_refused"))]
        assert {d["reason"] for d in refusals} == {"identity not verified strongly enough"}
        assert all(d["required_trust"] == 4 and d["trust"] == 2 for d in refusals)
