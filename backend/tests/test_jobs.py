"""The background job queue: due jobs run once, failures retry, crashed work is picked up again."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from superaide.db.models import AuditLog, Job
from superaide.db.session import session_scope
from superaide.jobs import queue
from superaide.jobs.queue import cancel, enqueue, handler, run_due
from superaide.jobs.worker import JobWorker

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


@pytest.fixture
def ran(monkeypatch):
    """Register throwaway handlers for this test only."""
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(queue, "HANDLERS", {})

    @handler("ok")
    def ok(session, job):
        calls.append(("ok", job.payload))

    @handler("boom")
    def boom(session, job):
        calls.append(("boom", job.payload))
        raise RuntimeError("upstream is down")

    return calls


def job(kind: str) -> Job:
    with session_scope() as s:
        row = s.scalar(select(Job).where(Job.kind == kind))
        assert row is not None
        s.expunge(row)
        return row


def test_a_due_job_runs_once_and_a_future_one_waits(ran):
    with session_scope() as s:
        enqueue(s, "ok", {"n": 1}, run_at=NOW - timedelta(seconds=1))
        enqueue(s, "ok", {"n": 2}, run_at=NOW + timedelta(hours=1))
    assert run_due(NOW) == 1
    assert run_due(NOW) == 0  # already done; not run twice
    assert ran == [("ok", {"n": 1})]
    assert run_due(NOW + timedelta(hours=1)) == 1
    assert [p["n"] for _, p in ran] == [1, 2]
    with session_scope() as s:
        assert {j.status for j in s.scalars(select(Job))} == {"done"}


def test_the_same_dedupe_key_is_enqueued_only_once(ran):
    with session_scope() as s:
        first = enqueue(s, "ok", {"n": 1}, run_at=NOW, dedupe_key="reminder:42")
        second = enqueue(s, "ok", {"n": 2}, run_at=NOW, dedupe_key="reminder:42")
        assert first is not None and second is None
    run_due(NOW)
    assert ran == [("ok", {"n": 1})]


def test_a_failing_job_retries_with_back_off_then_gives_up_and_is_audited(ran):
    with session_scope() as s:
        enqueue(s, "boom", run_at=NOW, max_attempts=3)
    assert run_due(NOW) == 1
    first = job("boom")
    assert first.status == "queued" and first.attempts == 1 and "upstream is down" in (first.last_error or "")
    assert first.run_at > datetime.now(UTC)  # backed off
    # Not due again until the back-off passes.
    assert run_due(datetime.now(UTC)) == 0
    for _ in range(2):
        run_due(job("boom").run_at + timedelta(seconds=1))
    final = job("boom")
    assert final.status == "failed" and final.attempts == 3 and final.finished_at is not None
    with session_scope() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == "job_failed")) is not None


def test_one_failing_job_does_not_stop_the_others(ran):
    with session_scope() as s:
        enqueue(s, "boom", run_at=NOW - timedelta(minutes=2))
        enqueue(s, "ok", {"n": 1}, run_at=NOW - timedelta(minutes=1))
    assert run_due(NOW) == 2
    assert job("ok").status == "done" and job("boom").status == "queued"


def test_work_left_running_by_a_crashed_worker_is_picked_up_again(ran):
    with session_scope() as s:
        j = enqueue(s, "ok", {"n": 1}, run_at=NOW - timedelta(hours=1))
        assert j is not None
        j.status, j.locked_at, j.attempts = "running", NOW - timedelta(minutes=30), 1
    assert run_due(NOW) == 1
    assert job("ok").status == "done" and job("ok").attempts == 2


def test_a_job_another_worker_is_running_is_left_alone(ran):
    with session_scope() as s:
        j = enqueue(s, "ok", run_at=NOW - timedelta(minutes=5))
        assert j is not None
        j.status, j.locked_at = "running", NOW - timedelta(minutes=1)  # fresh lock
    assert run_due(NOW) == 0 and ran == []


def test_a_queued_job_can_be_cancelled(ran):
    with session_scope() as s:
        enqueue(s, "ok", run_at=NOW, dedupe_key="reminder:7")
        assert cancel(s, "reminder:7") is True
        assert cancel(s, "reminder:7") is False  # nothing queued under that key any more
    assert run_due(NOW) == 0 and ran == []
    assert job("ok").status == "cancelled"


def test_a_job_deleted_after_it_was_claimed_is_skipped_without_stopping_the_worker(ran):
    # Rows can vanish between claiming and running (a database reset, as the e2e suite does
    # between tests): the rest still run and the worker's tick doesn't crash.
    @handler("wipe")
    def wipe(session, job):
        ran.append(("wipe", job.payload))
        session.execute(delete(Job).where(Job.kind != "wipe"))

    @handler("gone")
    def gone(session, job):
        session.execute(delete(Job).where(Job.id == job.id))
        session.flush()
        raise RuntimeError("failed after its row was deleted")

    with session_scope() as s:
        enqueue(s, "wipe", {}, run_at=NOW - timedelta(seconds=3))
        enqueue(s, "ok", {"n": 1}, run_at=NOW - timedelta(seconds=2))
    assert run_due(NOW) == 2
    assert ran == [("wipe", {})]  # the deleted job was skipped, not run
    with session_scope() as s:
        enqueue(s, "gone", {}, run_at=NOW - timedelta(seconds=1))
    assert run_due(NOW) == 1  # a failure for a job that no longer exists is only logged


def test_a_job_with_no_handler_fails_instead_of_looping(ran):
    with session_scope() as s:
        enqueue(s, "nobody-handles-this", run_at=NOW, max_attempts=1)
    run_due(NOW)
    assert job("nobody-handles-this").status == "failed"
    assert "no handler" in (job("nobody-handles-this").last_error or "")


def test_the_worker_thread_runs_due_jobs_and_stops_cleanly(ran):
    with session_scope() as s:
        enqueue(s, "ok", {"n": 1})
    worker = JobWorker(interval=0.05)
    worker.start()
    try:
        for _ in range(100):
            if ran:
                break
            time.sleep(0.02)
    finally:
        worker.stop()
    assert ran == [("ok", {"n": 1})]
