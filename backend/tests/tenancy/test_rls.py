"""ADR 0009 T1: Row-Level Security keeps each agent instance's rows apart inside Postgres.

The suite normally connects as a superuser, which Postgres exempts from RLS. These tests
create a plain role inside a transaction that is rolled back (roles, grants and policies
are all transactional in Postgres), so nothing outlives the test.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import Connection, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from superaide.db import rls
from superaide.db.models import AgentState, AuditLog, Base, Job, MemoryItem, Person, SpendEntry
from superaide.db.roles import RoleError, ensure_app_role
from superaide.db.session import get_engine
from superaide.tenancy.context import InstanceScope, use_instance

BACKEND = Path(__file__).resolve().parents[2]
A = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
B = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
CHECKED = ("person", "memory_item", "audit_log", "job", "spend_ledger", "agent_state")


def _seed(session: Session, scope: InstanceScope, label: str) -> None:
    ids = {"tenant_id": scope.tenant_id, "agent_id": scope.agent_id}
    person = Person(display_name=f"Owner {label}", relation="manager", **ids)
    session.add(person)
    session.flush()
    session.add(MemoryItem(content=f"{label}'s locker code", subject_person_ids=[person.id], **ids))
    session.add(AuditLog(actor="test", action=f"seed_{label}", detail={}, **ids))
    session.add(Job(kind="noop", payload={}, run_at=person.created_at, dedupe_key=f"rls-{label}", **ids))
    session.add(SpendEntry(purpose="chat", model="m", **ids))
    session.add(AgentState(key="frozen", value={"on": False}, **ids))
    session.flush()


@pytest.fixture
def probe() -> Iterator[Connection]:
    """A connection in one transaction: policies on, two instances' rows, then acting as a plain role."""
    with get_engine().connect() as conn:
        tx = conn.begin()
        try:
            rls.enable(conn)
            role = f"rls_probe_{uuid.uuid4().hex[:8]}"
            conn.execute(text(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}"))
            with Session(bind=conn, join_transaction_mode="create_savepoint") as s:
                _seed(s, A, "A")
                _seed(s, B, "B")
                s.commit()  # releases the savepoint; the outer transaction still rolls back
            conn.execute(text(f"SET LOCAL ROLE {role}"))
            yield conn
        finally:
            tx.rollback()


def _as(conn: Connection, scope: InstanceScope | None) -> None:
    agent = str(scope.agent_id) if scope else ""
    tenant = str(scope.tenant_id) if scope else ""
    conn.execute(text(rls.set_scope_sql()), {"agent": agent, "tenant": tenant})


def test_an_instance_sees_only_its_own_rows(probe):
    for scope in (A, B):
        _as(probe, scope)
        for table in CHECKED:
            seen = set(probe.scalars(text(f"SELECT DISTINCT agent_id FROM {table}")))
            assert seen == {scope.agent_id}, table


def test_without_a_scope_nothing_is_visible(probe):
    _as(probe, None)
    for table in rls.TABLES:
        if probe.scalar(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table}):
            assert probe.scalar(text(f"SELECT count(*) FROM {table}")) == 0, table


def test_an_instance_cannot_write_or_change_another_instances_rows(probe):
    _as(probe, A)
    with pytest.raises(DBAPIError, match="row-level security"), probe.begin_nested():
        probe.execute(
            text(
                "INSERT INTO audit_log (id, tenant_id, agent_id, created_at, actor, action, detail) "
                "VALUES (gen_random_uuid(), :t, :a, now(), 'x', 'smuggled', '{}')"
            ),
            {"t": B.tenant_id, "a": B.agent_id},
        )
    assert probe.execute(text("UPDATE memory_item SET content = 'overwritten'")).rowcount == 1  # only A's
    assert probe.execute(text("DELETE FROM person WHERE display_name = 'Owner B'")).rowcount == 0
    with pytest.raises(DBAPIError, match="row-level security"), probe.begin_nested():
        probe.execute(text("UPDATE person SET agent_id = :b"), {"b": B.agent_id})  # moving a row out


def test_the_plain_role_cannot_switch_policies_off(probe):
    with pytest.raises(DBAPIError, match="must be owner"), probe.begin_nested():
        probe.execute(text("ALTER TABLE person DISABLE ROW LEVEL SECURITY"))
    assert probe.scalar(text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user")) is False


def test_the_session_tells_postgres_which_instance_is_in_scope(probe):
    with use_instance(B), Session(bind=probe, join_transaction_mode="create_savepoint") as s:
        assert s.scalar(text("SELECT current_setting('app.agent_id', true)")) == str(B.agent_id)
        assert [p.display_name for p in s.scalars(select(Person))] == ["Owner B"]
        assert [m.content for m in s.scalars(select(MemoryItem))] == ["B's locker code"]


# --- every table with agent_id is covered --------------------------------------------------------


def test_every_table_with_agent_id_has_the_policy_or_a_reason_not_to():
    with_agent = {t.name for t in Base.metadata.sorted_tables if "agent_id" in t.c} | {
        "memory_embedding",
        "chunk_embedding",
    }
    assert with_agent - set(rls.TABLES) - set(rls.EXEMPT) == set(), "add new tables to db/rls.py and their migration"
    assert set(rls.TABLES) - with_agent == set()


def test_the_migrations_turn_the_policy_on_for_every_such_table():
    """Run every migration into a scratch schema and read the catalogue: what deploys is what's checked."""
    schema = f"rls_meta_{uuid.uuid4().hex[:8]}"
    url = get_engine().url.render_as_string(hide_password=False)
    with get_engine().begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    try:
        scoped = f"{url}{'&' if '?' in url else '?'}options={quote(f'-csearch_path={schema},public')}"
        env = {**os.environ, "DATABASE_URL": scoped, "MIGRATION_DATABASE_URL": ""}
        done = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, capture_output=True, text=True
        )
        assert done.returncode == 0, done.stderr[-2000:]
        with get_engine().connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                    "EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid AND p.polname = :policy) "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'agent_id' AND NOT a.attisdropped "
                    "WHERE n.nspname = :schema AND c.relkind = 'r'"
                ),
                {"schema": schema, "policy": rls.POLICY},
            ).all()
        assert {"person", "memory_item", "job", "memory_embedding", "instance_member"} <= {r[0] for r in rows}
        missing = sorted(r[0] for r in rows if r[0] not in rls.EXEMPT and not (r[1] and r[2] and r[3]))
        assert missing == [], f"tables with agent_id but no policy after migrating: {missing}"
    finally:
        with get_engine().begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))


# --- the app's own role ------------------------------------------------------------------------


def test_the_app_role_can_use_rows_but_not_bypass_policies_or_change_the_schema():
    with get_engine().connect() as conn:
        tx = conn.begin()
        try:
            name = f"app_probe_{uuid.uuid4().hex[:8]}"
            ensure_app_role(conn, name=name, password="x" * 24)
            ensure_app_role(conn, name=name, password="y" * 24)  # again: updates, doesn't fail
            flags = conn.execute(
                text(
                    "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolcanlogin "
                    "FROM pg_roles WHERE rolname = :n"
                ),
                {"n": name},
            ).one()
            assert tuple(flags) == (False, False, False, False, True)
            conn.execute(text(f"SET LOCAL ROLE {name}"))
            conn.execute(text("SELECT count(*) FROM person"))  # may read (RLS decides which rows)
            with pytest.raises(DBAPIError, match="permission denied"), conn.begin_nested():
                conn.execute(text("CREATE TABLE sneaky (id int)"))
            with pytest.raises(DBAPIError, match="must be owner"), conn.begin_nested():
                conn.execute(text("ALTER TABLE person DISABLE ROW LEVEL SECURITY"))
        finally:
            tx.rollback()


@pytest.mark.parametrize(("name", "password"), [("Robert'); DROP", "x" * 24), ("superaide_app", "short")])
def test_the_app_role_refuses_odd_names_and_short_passwords(name, password):
    with get_engine().connect() as conn, pytest.raises(RoleError):
        ensure_app_role(conn, name=name, password=password)
