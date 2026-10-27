"""Issues 24 and 25: accounts are platform-wide, so events about an account never land in someone else's Ava.

- An account that has not hired belongs to no instance: no Person, and its sign-up lines go to the
  platform's account log.
- Once it has its own Ava, its account events (password, email) are in that Ava's audit log.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, text

from superaide.identity.auth.models import AccountAudit, UserAccount
from superaide.identity.models import Person
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import get_engine, session_scope
from superaide.platform.tenancy.models import AgentInstance, InstanceMember
from superaide.platform.tenancy.service import move_stray_account_data
from tests.api_support import OTHER, OWNER, PASSWORD, hire, signup

NEW_PASSWORD = "another correct horse staple 2"


def user_id(email: str) -> uuid.UUID:
    with session_scope() as s:
        found = s.scalar(select(UserAccount.id).where(UserAccount.email_normalized == email))
        assert found is not None
        return found


def instance_id(email: str) -> uuid.UUID:
    with session_scope() as s:
        found = s.scalar(select(InstanceMember.agent_id).where(InstanceMember.user_id == user_id(email)))
        assert found is not None
        return found


def audit_lines(who: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    """(instance, action) of every audit_log line about this account, from any instance (not hiring itself)."""
    with session_scope() as s:
        rows = s.scalars(
            select(AuditLog).where(AuditLog.detail["user_id"].astext == str(who), AuditLog.action != "hired")
        ).all()
        return [(r.agent_id, r.action) for r in rows]


def platform_lines(who: uuid.UUID) -> list[str]:
    with session_scope() as s:
        rows = s.scalars(select(AccountAudit).where(AccountAudit.user_id == who).order_by(AccountAudit.created_at))
        return [r.action for r in rows]


def test_an_account_that_has_not_hired_is_stored_in_no_instance(browser):
    a, b = browser(), browser()
    assert signup(a).status_code == 200 and hire(a).status_code == 200
    assert signup(b, **OTHER).status_code == 200  # never hires

    ub = user_id(OTHER["email"])
    with session_scope() as s:
        assert s.get(UserAccount, ub).person_id is None
        assert s.scalars(select(Person).where(Person.display_name == OTHER["name"])).all() == []
    assert audit_lines(ub) == []  # nothing about B in A's Ava, or anywhere per-instance
    assert platform_lines(ub) == ["signup", "verification_sent"]
    me = b.get("/auth/me")
    assert me.status_code == 200 and me.json()["hire"] is None


def test_a_second_owners_account_events_are_in_their_own_ava(browser):
    a, b = browser(), browser()
    assert signup(a).status_code == 200 and hire(a).status_code == 200
    assert signup(b, **OTHER).status_code == 200 and hire(b).status_code == 200
    ia, ib = instance_id(OWNER["email"]), instance_id(OTHER["email"])
    assert ia != ib
    ub = user_id(OTHER["email"])

    assert b.post("/auth/password", json={"current": PASSWORD, "new": NEW_PASSWORD}).status_code == 200
    r = b.post("/auth/email", json={"email": "priya.new@example.com", "password": NEW_PASSWORD})
    assert r.status_code == 200

    lines = audit_lines(ub)
    assert sorted(a for _, a in lines) == ["email_changed", "password_changed", "verification_sent"]
    assert {i for i, _ in lines} == {ib}  # B's own Ava; none in A's
    assert audit_lines(user_id(OWNER["email"])) == []  # A did nothing since hiring
    # The lines from before B hired stay in the platform log, not in either Ava.
    assert platform_lines(ub) == ["signup", "verification_sent"]


def test_the_first_owners_own_account_events_stay_in_their_ava(browser):
    a = browser()
    assert signup(a).status_code == 200 and hire(a).status_code == 200
    assert a.post("/auth/password", json={"current": PASSWORD, "new": NEW_PASSWORD}).status_code == 200
    assert audit_lines(user_id(OWNER["email"])) == [(instance_id(OWNER["email"]), "password_changed")]


def test_an_account_that_has_not_hired_logs_password_changes_on_the_platform(browser):
    a, b = browser(), browser()
    assert signup(a).status_code == 200 and hire(a).status_code == 200
    assert signup(b, **OTHER).status_code == 200
    assert b.post("/auth/password", json={"current": PASSWORD, "new": NEW_PASSWORD}).status_code == 200
    ub = user_id(OTHER["email"])
    assert audit_lines(ub) == []
    assert platform_lines(ub) == ["signup", "verification_sent", "password_changed"]


def test_hiring_after_signing_up_makes_the_accounts_person_in_its_instance(browser):
    a = browser()
    assert signup(a).status_code == 200
    ua = user_id(OWNER["email"])
    with session_scope() as s:
        assert s.get(UserAccount, ua).person_id is None
    assert hire(a).status_code == 200
    with session_scope() as s:
        person = s.get(Person, s.get(UserAccount, ua).person_id)
        assert person is not None and person.relation == "manager"
        assert person.agent_id == s.scalar(select(InstanceMember.agent_id).where(InstanceMember.user_id == ua))
        assert s.scalars(select(Person).where(Person.display_name == OWNER["name"])).all() == [person]


# --- the migration that cleans up what sign-up used to leave in the first owner's Ava ----------------


def test_stray_sign_up_data_moves_out_of_the_first_owners_instance(browser):
    a, b = browser(), browser()
    assert signup(a).status_code == 200 and hire(a).status_code == 200
    assert signup(b, **OTHER).status_code == 200  # never hires
    ia, ua, ub = instance_id(OWNER["email"]), user_id(OWNER["email"]), user_id(OTHER["email"])
    # Rebuild how the old sign-up stored B: a Person and "auth" lines inside A's instance.
    with session_scope() as s:
        person = Person(display_name=OTHER["name"], relation="unknown", agent_id=ia, tenant_id=_tenant(s, ia))
        s.add(person)
        s.flush()
        s.get(UserAccount, ub).person_id = person.id
        for action in ("signup", "verification_sent"):
            s.add(
                AuditLog(
                    actor="auth", action=action, detail={"user_id": str(ub)}, agent_id=ia, tenant_id=person.tenant_id
                )
            )
        s.add(
            AuditLog(
                actor="auth",
                action="password_changed",
                detail={"user_id": str(ua)},
                agent_id=ia,
                tenant_id=person.tenant_id,
            )
        )
        s.execute(text("DELETE FROM account_audit"))

    with get_engine().begin() as conn:
        removed, moved = move_stray_account_data(conn)

    assert (removed, moved) == (1, 2)
    with session_scope() as s:
        assert s.get(UserAccount, ub).person_id is None
        assert s.scalars(select(Person).where(Person.display_name == OTHER["name"])).all() == []
    assert audit_lines(ub) == [] and sorted(platform_lines(ub)) == ["signup", "verification_sent"]
    assert audit_lines(ua) == [(ia, "password_changed")]  # the owner's own line stays where it is


def _tenant(s, agent_id: uuid.UUID) -> uuid.UUID:
    instance = s.get(AgentInstance, agent_id)
    assert instance is not None
    return instance.tenant_id
