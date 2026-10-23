"""ADR 0009 T2: platform-wide accounts, one Ava per account, and each request in its owner's instance.

Many owners is the default (ALLOW_MANY_OWNERS=true, decided 2026-10-22); the `many` fixture
says so explicitly, and single-owner mode (false) has its own test.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from superaide.config import DEFAULT_AGENT_ID, get_settings
from superaide.db.models import Context, Event, MemoryItem, Person, UserAccount
from superaide.db.session import session_scope
from superaide.tenancy.models import AgentInstance, InstanceMember, Tenant
from tests.api.conftest import OTHER, OWNER, hire, signup


@pytest.fixture
def many(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)


def account(email: str) -> UserAccount:
    with session_scope() as s:
        user = s.scalar(select(UserAccount).where(UserAccount.email_normalized == email))
        assert user is not None
        s.expunge(user)
        return user


def instance_of(email: str) -> AgentInstance:
    user = account(email)
    with session_scope() as s:
        member = s.scalar(select(InstanceMember).where(InstanceMember.user_id == user.id))
        assert member is not None
        instance = s.get(AgentInstance, member.agent_id)
        assert instance is not None
        s.expunge(instance)
        return instance


def two_owners(browser):
    a, b = browser(), browser()
    assert signup(a).status_code == 200 and hire(a).status_code == 200
    assert signup(b, **OTHER).status_code == 200 and hire(b).status_code == 200
    return a, b


def test_in_single_owner_mode_ava_works_for_one_account(browser, monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_many_owners", False)
    a, b = browser(), browser()
    signup(a), hire(a)
    signup(b, **OTHER)
    r = hire(b)
    assert r.status_code == 409 and OWNER["name"] not in r.text
    assert any(x["slug"] == "ava" and x["hired"] for x in b.get("/app/agents").json())


def test_each_owner_gets_their_own_instance_and_person(browser, many):
    a, b = two_owners(browser)
    ia, ib = instance_of(OWNER["email"]), instance_of(OTHER["email"])
    assert ia.id == DEFAULT_AGENT_ID  # the first hire keeps the server's original instance
    assert ib.id != ia.id and ib.tenant_id != ia.tenant_id
    with session_scope() as s:
        assert s.get(Tenant, ib.tenant_id) is not None
        pa = s.get(Person, account(OWNER["email"]).person_id)
        pb = s.get(Person, account(OTHER["email"]).person_id)
        assert pa is not None and pb is not None
        assert (pa.agent_id, pa.relation) == (ia.id, "manager")
        assert (pb.agent_id, pb.relation, pb.display_name) == (ib.id, "manager", OTHER["name"])
        # B's sign-up Person (made before B had an instance) is gone, not left behind in A's.
        assert s.scalars(select(Person).where(Person.display_name == OTHER["name"])).all() == [pb]
    assert a.get("/app/me").json()["person"]["name"] == OWNER["name"]
    assert b.get("/app/me").json()["person"]["name"] == OTHER["name"]
    # Nobody is told Ava is taken.
    assert not any(x["hired"] for x in b.get("/app/agents").json() if x["slug"] == "ava")


def test_hiring_again_keeps_the_same_instance(browser, many):
    _, b = two_owners(browser)
    first = instance_of(OTHER["email"]).id
    assert hire(b).status_code == 200
    assert instance_of(OTHER["email"]).id == first
    with session_scope() as s:
        assert len(s.scalars(select(InstanceMember)).all()) == 2


def test_what_an_owner_says_is_stored_in_their_own_instance(browser, many):
    a, b = two_owners(browser)
    ia, ib = instance_of(OWNER["email"]), instance_of(OTHER["email"])
    assert b.post("/app/chat", json={"text": "Remember that my bike lock code is zq9032"}).status_code == 200
    assert a.post("/app/chat", json={"text": "Remember that my locker code is zq4417"}).status_code == 200
    with session_scope() as s:
        by_text = {m.content: m.agent_id for m in s.scalars(select(MemoryItem))}
        assert by_text["my bike lock code is zq9032"] == ib.id
        assert by_text["my locker code is zq4417"] == ia.id
        web_b = s.scalar(select(Context).where(Context.external_id == f"web:{account(OTHER['email']).person_id}"))
        assert web_b is not None and web_b.agent_id == ib.id
        assert {e.agent_id for e in s.scalars(select(Event).where(Event.context_id == web_b.id))} == {ib.id}
    assert "zq9032" in b.get("/app/notebook").text and "zq9032" not in a.get("/app/notebook").text
    assert "zq4417" in a.get("/app/notebook").text and "zq4417" not in b.get("/app/notebook").text


def test_a_streamed_turn_runs_in_the_owners_instance(browser, many):
    _, b = two_owners(browser)
    ib = instance_of(OTHER["email"])
    with b.stream("POST", "/app/chat/stream", json={"text": "hello there"}) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    assert "event: done" in body
    with session_scope() as s:
        rows = s.scalars(select(Event).where(Event.content == "hello there")).all()
        assert rows and {e.agent_id for e in rows} == {ib.id}


def test_an_email_is_one_account_on_the_whole_server(browser):
    assert signup(browser()).status_code == 200
    again = signup(browser(), email=OWNER["email"].upper())
    assert again.status_code == 409
