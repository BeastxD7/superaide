"""ADR 0009 T8: the operator console is metadata by default; owner content is break-glass.

Seeing an owner's people, conversations, memories, approvals or log (or linking accounts)
needs a reason, names one instance, and is written to that owner's own log, which the
owner can read in the app.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from superaide.config import get_settings
from superaide.db.models import AuditLog, UserAccount
from superaide.db.session import session_scope
from superaide.tenancy.context import all_instances
from superaide.tenancy.models import InstanceMember
from tests.api.conftest import OTHER, OWNER, hire, signup

ADMIN = {"Authorization": "Bearer test-admin-token"}
REASON = "owner reported a missing reminder"
CONTENT = ["/api/people", "/api/conversations", "/api/events", "/api/memories", "/api/approvals", "/api/audit"]


@pytest.fixture
def owners(browser, monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)
    a, b = browser(), browser()
    signup(a), hire(a)
    signup(b, **OTHER), hire(b)
    assert a.post("/app/chat", json={"text": "Remember that my locker code is 4417"}).status_code == 200
    assert b.post("/app/chat", json={"text": "Remember that my bike lock code is 9032"}).status_code == 200
    return a, b


def instance_id(email: str) -> str:
    with session_scope() as s:
        user = s.scalar(select(UserAccount).where(UserAccount.email_normalized == email))
        assert user is not None
        member = s.scalar(select(InstanceMember).where(InstanceMember.user_id == user.id))
        assert member is not None
        return str(member.agent_id)


def accesses() -> list[AuditLog]:
    with all_instances(), session_scope() as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.action == "operator_access")).all()
        s.expunge_all()
        return list(rows)


@pytest.mark.parametrize("path", CONTENT)
@pytest.mark.parametrize("reason", [None, "", "because", "         x"])
def test_owner_content_needs_a_reason(browser, owners, path, reason):
    headers = ADMIN | ({"X-Operator-Reason": reason} if reason is not None else {})
    r = browser(csrf=False).get(path, headers=headers)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "reason_required"
    assert "4417" not in r.text and accesses() == []


def test_linking_needs_a_reason_too(browser, owners):
    c = browser()
    for action in ("link", "unlink"):
        r = c.post(f"/api/identities/00000000-0000-4000-8000-0000000000aa/{action}", json={}, headers=ADMIN)
        assert r.status_code == 403


def test_metadata_needs_no_reason_and_shows_no_owner(browser, owners):
    c = browser(csrf=False)
    for path in ("/api/overview", "/api/spend", "/api/instances"):
        assert c.get(path, headers=ADMIN).status_code == 200, path
    listed = c.get("/api/instances", headers=ADMIN).json()
    assert len(listed) == 2 and all(i["counts"]["memories"] >= 1 for i in listed)
    text = json.dumps(listed)
    for private in (OWNER["name"], OWNER["email"], OTHER["name"], OTHER["email"], "4417", "9032"):
        assert private not in text
    assert accesses() == []


def test_break_glass_opens_one_instance_and_tells_its_owner(browser, owners):
    a, b = owners
    c = browser(csrf=False)
    b_id = instance_id(OTHER["email"])
    r = c.get("/api/memories", headers=ADMIN | {"X-Operator-Reason": REASON, "X-Operator-Instance": b_id})
    assert r.status_code == 200
    assert "9032" in r.text and "4417" not in r.text  # only the instance named
    [row] = accesses()
    assert str(row.agent_id) == b_id and row.actor == "operator"
    assert row.detail["reason"] == REASON and row.detail["path"] == "/api/memories"
    # The owner sees it in the app; the other owner doesn't.
    seen = b.get("/app/access-log").json()
    assert seen == [{"at": seen[0]["at"], "what": "/api/memories", "reason": REASON}]
    assert a.get("/app/access-log").json() == []


def test_without_an_instance_named_the_servers_first_is_opened(browser, owners):
    a, _ = owners
    r = browser(csrf=False).get("/api/memories", headers=ADMIN | {"X-Operator-Reason": REASON})
    assert r.status_code == 200 and "4417" in r.text and "9032" not in r.text
    assert len(a.get("/app/access-log").json()) == 1


@pytest.mark.parametrize("instance", ["not-a-uuid", "00000000-0000-4000-8000-0000000000ff"])
def test_an_unknown_instance_is_refused(browser, owners, instance):
    r = browser(csrf=False).get(
        "/api/memories", headers=ADMIN | {"X-Operator-Reason": REASON, "X-Operator-Instance": instance}
    )
    assert r.status_code == 404 and accesses() == []
