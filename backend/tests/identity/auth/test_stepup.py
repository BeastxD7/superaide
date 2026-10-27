# ruff: noqa: F811
"""#451: sensitive changes are confirmed in two steps (a single-use nonce), for every kind of account."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.identity.auth import stepup
from superaide.identity.auth.models import UserAccount
from superaide.platform.config import get_settings
from superaide.platform.db.models import AgentState, AuditLog
from superaide.platform.db.session import session_scope
from superaide.policy.killswitch import set_state
from tests.integrations.gmail_fake import api as gmail_api  # noqa: F401
from tests.integrations.gmail_fake import connect, gmail_fake_fixture, mail, owner_client_fixture  # noqa: F401

URL = "/app/gmail/read-settings"
ACTION = "gmail_codes_read"
PASSWORD = "correct horse battery"


@pytest.fixture
def gm(owner_client, gmail_fake):
    connect(scopes=(gmail_api.READONLY, gmail_api.COMPOSE), drafts=True)
    return owner_client


def ask(client, action=ACTION):
    return client.post("/app/stepup", json={"action": action})


def audit_actions() -> list[str]:
    with session_scope() as s:
        return [a.action for a in s.scalars(select(AuditLog)).all()]


def test_a_confirmation_is_single_use(gm):
    nonce = ask(gm).json()["nonce"]
    assert gm.put(URL, json={"codes": "read", "confirmation": nonce}).status_code == 200
    assert gm.put(URL, json={"codes": "never"}).status_code == 200
    again = gm.put(URL, json={"codes": "read", "confirmation": nonce})  # the same nonce, a second time
    assert again.status_code == 403 and "already used" in again.json()["detail"]


def test_a_confirmation_for_another_action_another_session_another_account_or_expired_is_refused(gm, app=None):
    # another action: an unknown action cannot even get a nonce
    assert ask(gm, "delete_everything").status_code == 403
    # another session of the same account
    other = TestClient(gm.app, headers=gm.headers)
    assert other.post("/auth/login", json={"email": "s@example.com", "password": PASSWORD}).status_code == 200
    nonce = ask(other).json()["nonce"]
    refused = gm.put(URL, json={"codes": "read", "confirmation": nonce})
    assert refused.status_code == 403 and "another session" in refused.json()["detail"]
    # expired
    nonce = ask(gm).json()["nonce"]
    with session_scope() as s:
        row = s.scalars(select(AgentState).where(AgentState.key == stepup._key(nonce))).one()  # noqa: SLF001
        set_state(s, row.key, {**row.value, "expires": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()})
    expired = gm.put(URL, json={"codes": "read", "confirmation": nonce})
    assert expired.status_code == 403 and "expired" in expired.json()["detail"]
    reasons = [a.detail.get("reason") for a in _audits("stepup_refused")]
    assert {"other_session", "expired"} <= set(reasons)
    # nothing was changed by any of it
    assert gm.get(URL).json()["settings"]["codes"] == "never"


def _audits(action):
    with session_scope() as s:
        return [a for a in s.scalars(select(AuditLog)).all() if a.action == action]


def test_a_one_call_change_without_a_confirmation_is_refused(gm):
    r = gm.put(URL, json={"codes": "read"})
    assert r.status_code == 403 and "Confirm this change first" in r.json()["detail"]
    assert "stepup_refused" in audit_actions()


def test_a_google_only_account_can_confirm_without_ever_being_asked_for_a_password(gm):
    with session_scope() as s:
        user = s.scalars(select(UserAccount)).one()
        user.password_hash = ""  # how a Google-created account is stored
    got = ask(gm).json()
    assert got["method"] == "confirm" and got["what"]
    assert gm.put(URL, json={"codes": "read", "confirmation": got["nonce"]}).status_code == 200


def test_the_stricter_mode_asks_a_password_account_for_its_password_but_not_a_google_only_one(gm, monkeypatch):
    monkeypatch.setattr(get_settings(), "stepup_mode", "password")
    got = ask(gm).json()
    assert got["method"] == "password"
    wrong = gm.put(URL, json={"codes": "read", "confirmation": got["nonce"], "confirm_password": "nope-nope-nope"})
    assert wrong.status_code == 403 and "password isn't right" in wrong.json()["detail"]
    got = ask(gm).json()
    assert gm.put(URL, json={"codes": "read"}).status_code == 403
    missing = gm.put(URL, json={"codes": "read", "confirmation": got["nonce"]})
    assert missing.status_code == 403 and "Enter your password" in missing.json()["detail"]
    got = ask(gm).json()
    ok = gm.put(URL, json={"codes": "read", "confirmation": got["nonce"], "confirm_password": PASSWORD})
    assert ok.status_code == 200
    gm.put(URL, json={"codes": "never"})
    with session_scope() as s:
        s.scalars(select(UserAccount)).one().password_hash = ""
    got = ask(gm).json()
    assert got["method"] == "confirm"  # a Google-only account is never asked for a password
    assert gm.put(URL, json={"codes": "read", "confirmation": got["nonce"]}).status_code == 200


def test_failures_are_403_never_401_and_a_signed_out_client_gets_401(gm, app=None):
    assert gm.put(URL, json={"codes": "read", "confirmation": "x"}).status_code == 403
    anon = TestClient(gm.app, headers=gm.headers)
    assert anon.post("/app/stepup", json={"action": ACTION}).status_code == 401


def test_asking_is_audited_and_unknown_actions_get_no_nonce(gm):
    assert ask(gm).status_code == 200
    assert "stepup_requested" in audit_actions()
    assert ask(gm, "grant_everything").status_code == 403


def test_the_service_refuses_directly_too(gm):
    with session_scope() as s:
        user = s.scalars(select(UserAccount)).one()
        issued = stepup.issue(s, user=user, session_token="tok-a", action=ACTION)
        with pytest.raises(stepup.StepUpError) as e:
            stepup.consume(s, user=user, session_token="tok-b", action=ACTION, nonce=issued["nonce"])
        assert e.value.reason == "other_session"
        with pytest.raises(stepup.StepUpError) as e:
            stepup.consume(s, user=user, session_token="tok-a", action=ACTION, nonce=issued["nonce"])
        assert e.value.reason == "unknown_or_used"  # spent by the first (refused) try
