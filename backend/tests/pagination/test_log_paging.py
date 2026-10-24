"""The logs page by cursor (ADR 0014): the operator's safety log (/api/audit) and the owner's access log
(/app/access-log), both bare arrays with paging headers, with index plans proven on skewed data."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql

from superaide.app.accounts import owner as hired_owner
from superaide.main import create_app
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from superaide.platform.pagination import SortKey, bind_for, encode_cursor, page_query

ADMIN = {"Authorization": "Bearer test-admin-token", "X-Operator-Reason": "checking a reported problem"}
PASSWORD = "correct horse battery"
BASE = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def browser(agent):
    app = create_app(agent)

    def make() -> TestClient:
        return TestClient(app, headers={"X-Requested-With": "superaide"})

    return make


def hired(browser, email: str = "a@example.com") -> TestClient:
    c = browser()
    assert c.post("/auth/signup", json={"name": "Meera Iyer", "email": email, "password": PASSWORD}).status_code == 200
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c


def seed_operator_visits(n: int, start: int = 0) -> None:
    with session_scope() as s:
        for i in range(n):
            s.add(
                AuditLog(
                    actor="operator",
                    action="operator_access",
                    detail={"path": f"/api/people/{i}", "reason": f"reason number {i}"},
                    created_at=BASE + timedelta(minutes=start + i),
                )
            )


def walk(c: TestClient, path: str, headers=None, limit: int = 10) -> list[dict]:
    seen: list[dict] = []
    cursor = None
    while True:
        r = c.get(path, params={"limit": limit, **({"cursor": cursor} if cursor else {})}, headers=headers)
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)
        seen += r.json()
        cursor = r.headers.get("X-Next-Cursor")
        assert (r.headers["X-Has-More"] == "true") == (cursor is not None)
        if not cursor:
            return seen


def test_access_log_pages_every_visit_once_newest_first_and_keeps_its_shape(browser):
    c = hired(browser)
    seed_operator_visits(37)
    plain = c.get("/app/access-log")
    assert plain.status_code == 200 and isinstance(plain.json(), list) and len(plain.json()) == 37
    assert set(plain.json()[0]) == {"id", "at", "what", "reason"}
    rows = walk(c, "/app/access-log", limit=10)
    assert len(rows) == 37 == len({r["id"] for r in rows})
    assert [r["at"] for r in rows] == sorted((r["at"] for r in rows), reverse=True)
    assert c.get("/app/access-log", params={"limit": 5000}).status_code == 200  # clamped, as before
    assert len(c.get("/app/access-log", params={"limit": 5000}).json()) == 37


def test_access_log_cursor_is_bound_to_its_owner_and_refuses_junk(browser):
    a = hired(browser, "a@example.com")
    b = hired(browser, "b@example.com")
    seed_operator_visits(15)
    cursor = a.get("/app/access-log", params={"limit": 4}).headers["X-Next-Cursor"]
    r = b.get("/app/access-log", params={"cursor": cursor})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_cursor"
    assert a.get("/app/access-log", params={"cursor": "junk"}).status_code == 422
    assert a.get("/app/access-log", params={"cursor": cursor[:-3] + "AAA"}).status_code == 422


def test_only_operator_visits_are_in_the_access_log(browser):
    c = hired(browser)
    seed_operator_visits(3)
    with session_scope() as s:
        s.add(AuditLog(actor="system", action="llm_call", detail={"path": "/x", "reason": "no"}))
    assert len(walk(c, "/app/access-log", limit=2)) == 3


def test_safety_log_pages_by_cursor_and_keeps_the_bare_array(browser):
    api = browser()
    with session_scope() as s:
        for i in range(53):
            s.add(
                AuditLog(
                    actor="system", action="stranger_message", detail={"n": i}, created_at=BASE + timedelta(minutes=i)
                )
            )
    plain = api.get("/api/audit", headers=ADMIN)
    assert plain.status_code == 200 and isinstance(plain.json(), list)
    assert sum(1 for r in plain.json() if r["action"] == "stranger_message") == 53  # plus the operator's own visit
    rows = walk(api, "/api/audit", headers=ADMIN, limit=10)
    mine = [r for r in rows if r["action"] == "stranger_message"]
    assert len(mine) == 53 and len({r["id"] for r in rows}) == len(rows)
    assert len(api.get("/api/audit", params={"limit": 100000}, headers=ADMIN).json()) <= 200  # clamped at 200
    assert api.get("/api/audit", params={"cursor": "junk"}, headers=ADMIN).status_code == 422
    assert api.get("/api/audit").status_code == 401
    # a safety-log cursor does not open the events list
    cursor = api.get("/api/audit", params={"limit": 5}, headers=ADMIN).headers["X-Next-Cursor"]
    assert api.get("/api/events", params={"cursor": cursor}, headers=ADMIN).status_code == 422


def explain(s, stmt) -> str:
    c = stmt.compile(dialect=postgresql.dialect())
    return str(s.connection().exec_driver_sql("EXPLAIN (FORMAT JSON) " + str(c), c.params).scalar())


def test_log_queries_use_their_indexes_on_skewed_data(browser):
    """A log that is mostly model calls and identity checks, with a thin slice of operator visits, and three other
    instances holding three times as many rows over the same time range."""
    hired(browser)
    with session_scope() as s:
        owner = hired_owner(s)
        agent_id, tenant_id = owner.agent_id, owner.tenant_id
        mine = {"tenant_id": tenant_id, "agent_id": agent_id}
        s.bulk_insert_mappings(
            AuditLog,
            [
                {
                    "id": uuid.uuid4(),
                    "actor": "system",
                    "action": "llm_call",
                    "detail": {},
                    "created_at": BASE + timedelta(seconds=i),
                    **mine,
                }
                for i in range(30000)
            ],
        )
        s.bulk_insert_mappings(
            AuditLog,
            [
                {
                    "id": uuid.uuid4(),
                    "actor": "operator",
                    "action": "operator_access",
                    "detail": {},
                    "created_at": BASE + timedelta(seconds=i * 10),
                    **mine,
                }
                for i in range(1500)
            ],
        )
        for _ in range(3):
            other = {"tenant_id": tenant_id, "agent_id": uuid.uuid4()}
            s.bulk_insert_mappings(
                AuditLog,
                [
                    {
                        "id": uuid.uuid4(),
                        "actor": "system",
                        "action": "llm_call",
                        "detail": {},
                        "created_at": BASE + timedelta(seconds=i),
                        **other,
                    }
                    for i in range(30000)
                ],
            )
    keys = [SortKey(AuditLog.created_at), SortKey(AuditLog.id)]
    mid = encode_cursor(
        [BASE + timedelta(seconds=15000), uuid.UUID(int=2**127)], keys, endpoint="l", bind=bind_for("o")
    )
    with session_scope() as s:
        s.execute(text("ANALYZE audit_log"))
        for cursor in (None, mid):
            plan = explain(
                s,
                page_query(
                    select(AuditLog).where(AuditLog.agent_id == agent_id),
                    keys,
                    endpoint="l",
                    bind=bind_for("o"),
                    limit=200,
                    cursor=cursor,
                ),
            )
            assert "ix_audit_log_agent_created" in plan and "'Sort'" not in plan and "Seq Scan" not in plan, plan
            plan = explain(
                s,
                page_query(
                    select(AuditLog).where(
                        AuditLog.agent_id == agent_id,
                        AuditLog.actor == "operator",
                        AuditLog.action == "operator_access",
                    ),
                    keys,
                    endpoint="l",
                    bind=bind_for("o"),
                    limit=100,
                    cursor=cursor,
                ),
            )
            assert "ix_audit_log_operator_access" in plan and "'Sort'" not in plan and "Seq Scan" not in plan, plan
