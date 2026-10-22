"""ADR 0009 T5: jobs belong to an agent instance, are claimed fairly, and run in their instance's scope."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from superaide.config import get_settings
from superaide.db.models import AuditLog, Job
from superaide.db.session import get_engine, session_scope
from superaide.jobs import queue
from superaide.jobs.worker import schedule_recurring
from superaide.main import create_app
from superaide.tenancy.context import InstanceScope, current_scope, use_instance

A = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
B = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
PAST = datetime.now(UTC) - timedelta(minutes=1)


def add(scope: InstanceScope, n: int = 1, kind: str = "probe_scope", key: str | None = None) -> list[Job | None]:
    with use_instance(scope), session_scope() as s:
        return [queue.enqueue(s, kind, {"n": i}, run_at=PAST, dedupe_key=key) for i in range(n)]


@pytest.fixture
def seen() -> list[InstanceScope | None]:
    """A handler that records the scope it ran in, and writes an audit row (which takes that scope)."""
    log: list[InstanceScope | None] = []

    def probe(session, job: Job) -> None:
        log.append(current_scope())
        from superaide.audit import audit

        audit(session, "test", "probe_ran", job_id=job.id)

    queue.HANDLERS["probe_scope"] = probe
    yield log
    queue.HANDLERS.pop("probe_scope", None)


def test_a_job_belongs_to_the_instance_in_scope_when_queued():
    [job] = add(A)
    assert job is not None and (job.agent_id, job.tenant_id) == (A.agent_id, A.tenant_id)


def test_each_job_runs_inside_its_own_instance(seen):
    add(A), add(B)
    assert queue.run_due() == 2
    assert sorted(str(s.agent_id) for s in seen if s) == sorted([str(A.agent_id), str(B.agent_id)])
    with session_scope() as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.action == "probe_ran")).all()
        assert {r.agent_id for r in rows} == {A.agent_id, B.agent_id}
        assert {j.status for j in s.scalars(select(Job))} == {"done"}


def test_one_busy_owner_cannot_crowd_out_another(seen):
    add(A, 30)
    add(B, 1)
    with session_scope() as s:
        claimed = queue._claim(s, datetime.now(UTC), 5)
    agents = [scope.agent_id for _, _, scope in claimed]
    assert B.agent_id in agents and len(agents) == 5
    assert agents.count(A.agent_id) == 4


def test_no_instance_gets_more_than_its_share_of_a_batch(seen):
    add(A, 30)
    with session_scope() as s:
        assert len(queue._claim(s, datetime.now(UTC), 20)) == queue.PER_INSTANCE


def test_dedupe_keys_are_per_instance():
    [a1] = add(A, key="digest:today")
    [a2] = add(A, key="digest:today")
    [b1] = add(B, key="digest:today")
    assert a1 is not None and a2 is None and b1 is not None
    with use_instance(B), session_scope() as s:
        assert queue.cancel(s, "digest:today")
    with session_scope() as s:
        by_agent = {j.agent_id: j.status for j in s.scalars(select(Job))}
        assert by_agent == {A.agent_id: "queued", B.agent_id: "cancelled"}


def test_a_stale_running_job_is_claimed_again(seen):
    [job] = add(A)
    assert job is not None
    with session_scope() as s:
        s.execute(
            text("UPDATE job SET status = 'running', locked_at = :t WHERE id = :id"),
            {"t": datetime.now(UTC) - queue.STALE_AFTER - timedelta(minutes=1), "id": job.id},
        )
    assert queue.run_due() == 1 and len(seen) == 1


def test_the_app_role_claims_every_instances_jobs_only_through_the_function():
    add(A), add(B)
    with get_engine().connect() as conn:
        tx = conn.begin()
        try:
            from superaide.db import rls

            rls.enable(conn)
            role = f"jobs_probe_{uuid.uuid4().hex[:8]}"
            conn.execute(text(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}"))
            conn.execute(
                text(f"GRANT EXECUTE ON FUNCTION claim_due_jobs(timestamptz, integer, integer, interval) TO {role}")
            )
            conn.execute(text(f"SET LOCAL ROLE {role}"))
            assert conn.scalar(text("SELECT count(*) FROM job")) == 0  # no scope: the table shows nothing
            rows = conn.execute(
                text("SELECT agent_id FROM claim_due_jobs(now(), 10, 5, interval '10 minutes')")
            ).scalars()
            assert set(rows) == {A.agent_id, B.agent_id}
        finally:
            tx.rollback()


def test_recurring_work_is_scheduled_for_every_hired_instance(agent, monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)
    password = "correct horse battery"
    for name, email in (("Shashank", "s@example.com"), ("Priya", "p@example.com")):
        c = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
        c.post("/auth/signup", json={"name": name, "email": email, "password": password, "timezone": "Asia/Kolkata"})
        assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    schedule_recurring()
    with session_scope() as s:
        digests = s.scalars(select(Job).where(Job.kind == "send_digest")).all()
        assert len({j.agent_id for j in digests}) == 2
