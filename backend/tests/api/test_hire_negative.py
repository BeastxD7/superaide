"""Hiring Ava over HTTP: who may, what's refused, and that a refusal reveals nothing about the owner."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from superaide.app import accounts
from superaide.db.models import AgentHire, Onboarding, Person, UserAccount
from superaide.db.session import session_scope
from tests.api.conftest import OTHER, OWNER, hire, signup


def _hires() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(AgentHire)) or 0


def test_hiring_needs_an_account(browser):
    r = hire(browser())
    assert r.status_code == 401 and r.json()["detail"] == "Please log in."
    assert _hires() == 0


@pytest.mark.parametrize("accepted", [False, "false", "no", 0])
def test_hiring_needs_the_notice_accepted(browser, accepted):
    c = browser()
    signup(c)
    r = hire(c, accepted=accepted)
    assert r.status_code == 400 and "accept" in r.json()["detail"]
    assert _hires() == 0
    assert c.get("/auth/me").json()["hire"] is None


@pytest.mark.parametrize(
    "body", [{"agent": "ava"}, {"agent": "ava", "accepted_notice": "maybe"}, {"agent": 7, "accepted_notice": True}]
)
def test_malformed_hire_requests_are_refused(browser, body):
    c = browser()
    signup(c)
    assert c.post("/app/hire", json=body).status_code == 422
    assert _hires() == 0


@pytest.mark.parametrize(
    "agent", ["leo", "kai", "zed", "", "AVA", "ava ", "../ava", pytest.param("x" * 5000, id="5000-chars")]
)
def test_unknown_or_coming_soon_agents_cannot_be_hired(browser, agent):
    c = browser()
    signup(c)
    for accepted in (True, False):
        r = hire(c, agent=agent, accepted=accepted)
        assert r.status_code == 404 and r.json()["detail"] == "That agent can't be hired yet"
    assert _hires() == 0


@pytest.fixture
def single_owner(monkeypatch):
    """ALLOW_MANY_OWNERS=false: a self-hosted server for one person (ADR 0009)."""
    from superaide.config import get_settings

    monkeypatch.setattr(get_settings(), "allow_many_owners", False)


def test_a_second_account_hires_its_own_ava_and_learns_nothing_about_the_first(browser, owner):
    other = browser()
    me = signup(other, **OTHER).json()
    r = hire(other)
    assert r.status_code == 200 and r.json()["person"]["name"] == OTHER["name"]
    with session_scope() as s:
        owner_account = s.scalar(select(UserAccount).where(UserAccount.email_normalized == OWNER["email"]))
        secrets = [OWNER["email"], "Shashank", "Rao", str(owner_account.id), str(owner_account.person_id)]
    for secret in secrets:
        assert secret not in r.text, secret
    assert _hires() == 2
    assert other.get("/auth/me").json()["hire"]["agent"] == "ava"
    with session_scope() as s:
        mine = s.get(Person, s.get(UserAccount, me["user"]["id"]).person_id)
        theirs = s.get(Person, owner_account.person_id)
        assert mine.relation == theirs.relation == "manager" and mine.agent_id != theirs.agent_id


def test_in_single_owner_mode_a_second_account_gets_409_and_learns_nothing_about_the_owner(
    browser, owner, single_owner
):
    other = browser()
    me = signup(other, **OTHER).json()
    r = hire(other)
    assert r.status_code == 409
    with session_scope() as s:
        owner_account = s.scalar(select(UserAccount).where(UserAccount.email_normalized == OWNER["email"]))
        secrets = [OWNER["email"], "Shashank", "Rao", str(owner_account.id), str(owner_account.person_id)]
    for secret in secrets:
        assert secret not in r.text, secret
    # Nothing changed for either of them.
    assert _hires() == 1
    assert other.get("/auth/me").json()["hire"] is None
    with session_scope() as s:
        assert s.get(Person, owner_account.person_id).relation == "manager"
        other_account = s.get(UserAccount, me["user"]["id"])
        assert s.get(Person, other_account.person_id).relation == "unknown"


def test_in_single_owner_mode_two_accounts_hiring_at_once_get_a_clean_409(browser, owner, single_owner, monkeypatch):
    """The unique index is the last line of defence when both pass the "is anyone hired?" check."""
    other = browser()
    signup(other, **OTHER)
    monkeypatch.setattr(accounts, "owner_account", lambda session, agent_slug="ava": None)
    r = hire(other)
    assert r.status_code == 409 and "someone else" in r.json()["detail"]
    assert _hires() == 1
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Person).where(Person.relation == "manager")) == 1


def test_hiring_again_is_idempotent_and_keeps_progress(owner):
    first = owner.get("/app/messages").json()
    view = owner.post("/app/onboarding", json={"step": "hello", "answer": {"choice": "Nice to meet you"}}).json()
    assert view["step"] == "about"
    before = owner.get("/app/messages").json()
    again = hire(owner)
    assert again.status_code == 200
    assert again.json()["person"]["name"] == OWNER["name"]
    assert _hires() == 1
    assert owner.get("/app/me").json()["onboarding"]["step"] == "about"  # not reset to hello
    after = owner.get("/app/messages").json()
    assert len(after) == len(before) and len(first) < len(before)  # no second introduction
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Onboarding)) == 1


def test_hiring_again_still_needs_the_notice(owner):
    assert hire(owner, accepted=False).status_code == 400
    assert owner.get("/app/me").status_code == 200  # but the existing hire stands


def test_after_logout_hiring_needs_an_account_again(owner):
    owner.post("/auth/logout")
    assert hire(owner).status_code == 401
