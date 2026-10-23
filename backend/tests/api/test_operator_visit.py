"""One operator visit is one entry in the owner's access log (#286); the owner's sign-in can't be unlinked (#285)."""

from __future__ import annotations

import pytest
from tests.api.conftest import hire, signup

ADMIN = {"Authorization": "Bearer test-admin-token"}
REASON = "checking a reported problem"


@pytest.fixture
def owner_app(browser):
    c = browser()
    signup(c), hire(c)
    return c


def test_polling_a_screen_writes_one_access_entry_not_one_per_poll(owner_app):
    visit = ADMIN | {"X-Operator-Reason": REASON}
    for _ in range(7):  # the console refreshes People every 5 seconds
        assert owner_app.get("/api/people", headers=visit).status_code == 200
    seen = owner_app.get("/app/access-log").json()
    assert [(e["what"], e["reason"]) for e in seen] == [("/api/people", REASON)]
    # Another screen, or the same screen for another reason, is its own entry.
    assert owner_app.get("/api/conversations", headers=visit).status_code == 200
    assert owner_app.get("/api/people", headers=ADMIN | {"X-Operator-Reason": "a different reason"}).status_code == 200
    assert len(owner_app.get("/app/access-log").json()) == 3


def test_a_change_by_the_operator_is_always_logged(owner_app):
    visit = ADMIN | {"X-Operator-Reason": REASON}
    me = [p for p in owner_app.get("/api/people", headers=visit).json() if p["identities"]][0]
    web = me["identities"][0]["id"]
    for _ in range(2):
        assert owner_app.post(f"/api/identities/{web}/unlink", headers=visit).status_code == 409
    posts = [e for e in owner_app.get("/app/access-log").json() if "unlink" in e["what"]]
    assert len(posts) == 2


def test_the_owners_own_sign_in_cannot_be_unlinked(owner_app):
    visit = ADMIN | {"X-Operator-Reason": REASON}
    me = [p for p in owner_app.get("/api/people", headers=visit).json() if p["identities"]][0]
    [web] = [i for i in me["identities"] if i["provider"] == "web"]
    r = owner_app.post(f"/api/identities/{web['id']}/unlink", headers=visit)
    assert r.status_code == 409 and "own sign-in" in r.json()["detail"]
    after = [p for p in owner_app.get("/api/people", headers=visit).json() if p["identities"]][0]
    assert [i["provider"] for i in after["identities"]] == ["web"]
    reply = owner_app.post("/app/chat", json={"text": "what do you know about me?"}).json()
    assert "don't recognise this account" not in str(reply)
