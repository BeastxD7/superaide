"""Resume, the halt word and the operator's kill switch racing each other (#281): never an error, never a half state.

Threads call the real routes on separate clients of one app; nothing sleeps. The rules under test:
- the owner's resume lifts only the owner's own pause (idempotent), never the operator's platform pause;
- the console's kill on=false lifts the platform flag and every instance's own freeze (see the PR note on that).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from superaide.config import get_settings
from superaide.db.models import AuditLog
from superaide.db.session import session_scope
from superaide.policy.killswitch import instance_frozen, platform_frozen, set_frozen
from superaide.tenancy.context import InstanceScope, use_instance
from superaide.tenancy.models import AgentInstance
from tests.api_support import hire, login, signup

ADMIN = {"Authorization": "Bearer test-admin-token"}
THREADS = 8


@pytest.fixture
def breaker(monkeypatch):
    e = get_settings()
    monkeypatch.setattr(e, "circuit_breaker_enabled", True)
    monkeypatch.setattr(e, "allow_many_owners", True)
    return e


def owner_clients(browser, n: int):
    """One account, n signed-in clients (each its own session) of the same hired Ava."""
    first = browser()
    assert signup(first).status_code == 200 and hire(first).status_code == 200
    others = [browser() for _ in range(n - 1)]
    for c in others:
        assert login(c).status_code == 200
    return [first, *others]


def run_parallel(calls):
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return [f.result() for f in [pool.submit(c) for c in calls]]


def state(client):
    me = client.get("/app/me").json()
    return me["paused"], me["can_resume"]


def test_parallel_resumes_and_halts_end_in_a_consistent_state(browser, breaker):
    clients = owner_clients(browser, THREADS)
    halt = breaker.halt_word
    calls = []
    for i, c in enumerate(clients):
        calls.append(
            (lambda c=c: c.post("/app/resume")) if i % 2 else (lambda c=c: c.post("/app/chat", json={"text": halt}))
        )
    results = run_parallel(calls)
    assert all(r.status_code == 200 for r in results), [(r.status_code, r.text[:80]) for r in results]
    paused, can_resume = state(clients[0])
    assert can_resume == paused  # an owner's own pause is always hers to lift; never a pause nobody can lift
    # Resume is idempotent: twice more, the same answer, and Ava is running.
    for _ in range(2):
        r = clients[0].post("/app/resume")
        assert r.status_code == 200 and r.json() == {"paused": False}
    assert state(clients[0]) == (False, False)


def test_the_operators_pause_always_wins_over_a_racing_resume(browser, breaker):
    clients = owner_clients(browser, THREADS)
    calls = [lambda: clients[0].post("/api/kill", json={"on": True}, headers=ADMIN)]
    calls += [lambda c=c: c.post("/app/resume") for c in clients[1:]]
    results = run_parallel(calls)
    assert results[0].status_code == 200
    assert all(r.status_code in (200, 409) for r in results[1:])  # a no-op or a refusal, never an error
    assert state(clients[0]) == (True, False)  # the operator's pause stands, and it isn't the owner's to lift
    assert clients[0].post("/app/resume").status_code == 409
    with session_scope() as s:
        assert platform_frozen(s) is True


def test_console_resume_racing_a_halt_never_leaves_a_pause_nobody_can_lift(browser, breaker):
    clients = owner_clients(browser, THREADS)
    halt = breaker.halt_word
    calls = [lambda c=c: c.post("/api/kill", json={"on": False}, headers=ADMIN) for c in clients[:3]]
    calls += [lambda c=c: c.post("/app/chat", json={"text": halt}) for c in clients[3:6]]
    results = run_parallel(calls)
    assert all(r.status_code == 200 for r in results)
    paused, can_resume = state(clients[0])
    assert can_resume == paused  # either running, or paused by the owner's own halt, which she can lift
    with session_scope() as s:
        assert platform_frozen(s) is False


def hired_scopes() -> list[InstanceScope]:
    with session_scope() as s:
        return [
            InstanceScope(tenant_id=i.tenant_id, agent_id=i.id)
            for i in s.scalars(select(AgentInstance).order_by(AgentInstance.created_at))
        ]


def test_console_resume_with_twenty_instances(browser, breaker):
    for k in range(20):
        c = browser(ip=f"10.0.{k}.1")
        mail = f"owner{k}@example.com"
        assert signup(c, name=f"Owner {k}", email=mail).status_code == 200 and hire(c).status_code == 200
    scopes = hired_scopes()
    assert len(scopes) == 20
    own_pause = scopes[::2]  # ten owners halted their own Ava
    for scope in own_pause:
        with use_instance(scope), session_scope() as s:
            set_frozen(s, True, actor="halt_word")
    operator = browser(csrf=False)
    assert operator.post("/api/kill", json={"on": True}, headers=ADMIN).status_code == 200
    # Pausing everything touches no instance's own state.
    for scope in scopes:
        with use_instance(scope), session_scope() as s:
            assert instance_frozen(s, scope) == (scope in own_pause)
    assert operator.post("/api/kill", json={"on": False}, headers=ADMIN).status_code == 200
    with session_scope() as s:
        assert platform_frozen(s) is False
    for scope in scopes:
        with use_instance(scope), session_scope() as s:
            assert instance_frozen(s, scope) is False  # the console's resume also lifts every instance's own pause
    with session_scope() as s:
        cleared = s.scalars(
            select(AuditLog).where(AuditLog.action == "kill_switch", AuditLog.actor == "dashboard")
        ).all()
        assert sum(1 for r in cleared if (r.detail or {}).get("scope") == "instance") == len(own_pause)
