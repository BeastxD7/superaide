"""ADR 0009 T0: the instance scope, the tenancy tables and the migration of today's tenant."""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import delete, select, text

from superaide.app import accounts
from superaide.auth.service import Client, signup
from superaide.config import DEFAULT_AGENT_ID, DEFAULT_TENANT_ID
from superaide.db.models import Person, UserAccount
from superaide.db.session import get_engine, session_scope
from superaide.tenancy.context import (
    InstanceScope,
    NoScope,
    current_scope,
    default_scope,
    require_scope,
    scope_or_default,
    use_instance,
)
from superaide.tenancy.models import AgentInstance, InstanceMember, Tenant
from superaide.tenancy.service import AmbiguousTenancy, backfill_default_instance, instances_of

PASSWORD = "correct horse battery staple"


def _scope() -> InstanceScope:
    return InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())


def _hired_owner(email: str = "s@example.com", tz: str = "Asia/Kolkata") -> uuid.UUID:
    with session_scope() as s:
        user, _ = signup(s, email=email, password=PASSWORD, name="Shashank Rao", client=Client())
        user.timezone = tz
        accounts.hire(s, user)
        return user.id


# --- the scope -------------------------------------------------------------------------


def test_no_scope_by_default_and_require_scope_says_so():
    assert current_scope() is None
    with pytest.raises(NoScope):
        require_scope()
    assert scope_or_default() == default_scope() == InstanceScope(DEFAULT_TENANT_ID, DEFAULT_AGENT_ID)


def test_use_instance_nests_and_restores_even_on_error():
    a, b = _scope(), _scope()
    with use_instance(a):
        assert require_scope() == a
        with pytest.raises(RuntimeError), use_instance(b):
            assert require_scope() == b
            raise RuntimeError("boom")
        assert require_scope() == a
    assert current_scope() is None


def test_a_scope_never_leaks_into_another_thread():
    seen: list[InstanceScope | None] = []
    with use_instance(_scope()):
        t = threading.Thread(target=lambda: seen.append(current_scope()))
        t.start()
        t.join()
    assert seen == [None]


# --- hiring records the instance ----------------------------------------------------------


def test_hiring_creates_the_tenant_instance_and_owner_membership():
    user_id = _hired_owner()
    with session_scope() as s:
        user = s.get(UserAccount, user_id)
        assert user is not None
        tenant = s.get(Tenant, DEFAULT_TENANT_ID)
        instance = s.get(AgentInstance, DEFAULT_AGENT_ID)
        assert tenant is not None and tenant.default_timezone == "Asia/Kolkata"
        assert instance is not None
        assert (instance.agent_slug, instance.owner_user_id, instance.status, instance.tenant_id) == (
            "ava",
            user_id,
            "active",
            DEFAULT_TENANT_ID,
        )
        [(found, member)] = instances_of(s, user_id)
        assert found.id == DEFAULT_AGENT_ID
        assert (member.person_id, member.role) == (user.person_id, "owner")
        # Hiring again (the same owner) adds nothing.
        accounts.hire(s, user)
        s.flush()
        assert len(s.scalars(select(InstanceMember)).all()) == 1


def test_an_account_that_has_not_hired_belongs_to_no_instance():
    with session_scope() as s:
        user, _ = signup(s, email="p@example.com", password=PASSWORD, name="Priya Tan", client=Client())
        assert instances_of(s, user.id) == []


# --- migrating today's single tenant --------------------------------------------------------


def _forget_instances() -> None:
    with session_scope() as s:
        s.execute(delete(InstanceMember))
        s.execute(delete(AgentInstance))
        s.execute(delete(Tenant))


def test_backfill_recreates_the_instance_for_the_existing_hire_without_touching_data():
    user_id = _hired_owner()
    with session_scope() as s:
        people_before = s.scalar(text("SELECT count(*) FROM person"))
    _forget_instances()
    with get_engine().begin() as conn:
        assert backfill_default_instance(conn) == 1
        assert backfill_default_instance(conn) == 0  # safe to run twice
    with session_scope() as s:
        instance = s.get(AgentInstance, DEFAULT_AGENT_ID)
        assert instance is not None and instance.owner_user_id == user_id and instance.timezone == "Asia/Kolkata"
        assert s.scalar(text("SELECT count(*) FROM person")) == people_before
        [member] = s.scalars(select(InstanceMember)).all()
        assert member.user_id == user_id and member.role == "owner"


def test_backfill_on_a_fresh_install_creates_nothing():
    with get_engine().begin() as conn:
        assert backfill_default_instance(conn) == 0
    with session_scope() as s:
        assert s.scalars(select(AgentInstance)).all() == []


def test_backfill_refuses_to_guess_when_data_has_two_agent_ids():
    _hired_owner()
    _forget_instances()
    with session_scope() as s:
        s.add(Person(display_name="Someone else's contact", agent_id=uuid.uuid4()))
    with pytest.raises(AmbiguousTenancy), get_engine().begin() as conn:
        backfill_default_instance(conn)
