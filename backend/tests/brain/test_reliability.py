"""Job chains, reminder delivery, stale claims and the spend ledger when things go wrong (issues #84, #86, #91)."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.brain import platform_spend
from superaide.brain.budget import BudgetGuard
from superaide.brain.pricing import UsageCounts
from superaide.brain.providers.base import Priced, mark_billable, metered
from superaide.db.models import Intention, Job, SpendEntry
from superaide.db.session import session_scope
from superaide.intentions import service as intentions
from superaide.jobs import queue
from superaide.jobs.handlers import load
from superaide.jobs.queue import enqueue, handler, run_due
from superaide.jobs.worker import JobWorker, schedule_recurring
from superaide.knowledge.store import preferences_for
from superaide.main import create_app
from superaide.proactive import digest, prep

PASSWORD = "correct horse battery"
NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)

load()


@pytest.fixture
def owner(agent):
    c = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    body = {"name": "Jordan Whitfield", "email": "s@example.com", "password": PASSWORD, "timezone": "Asia/Singapore"}
    c.post("/auth/signup", json=body)
    c.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    return c


def jobs_of(kind: str, *statuses: str) -> list[Job]:
    with session_scope() as s:
        q = select(Job).where(Job.kind == kind)
        if statuses:
            q = q.where(Job.status.in_(statuses))
        rows = list(s.scalars(q.order_by(Job.run_at)))
        s.expunge_all()
        return rows


# --- #86: recurring chains survive a failing job ---------------------------------------------


def test_the_next_digest_is_queued_even_when_this_one_fails(owner, monkeypatch):
    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        preferences_for(s, person).digest = "morning"
        first = digest.schedule(s, person)
        assert first is not None
        first_at = first.run_at

    def boom(*a, **k):
        raise RuntimeError("compose is broken today")

    monkeypatch.setattr(digest, "compose", boom)
    assert run_due(first_at + timedelta(seconds=1)) == 1
    assert jobs_of("send_digest", "queued", "running")  # the retry of this one
    later = [j for j in jobs_of("send_digest") if j.run_at > first_at + timedelta(hours=1)]
    assert len(later) == 1  # tomorrow's is already booked


def test_the_next_scan_is_queued_even_when_this_one_fails(owner, monkeypatch):
    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        job = prep.schedule_scan(s, person)
        assert job is not None
        at = job.run_at

    def boom(*a, **k):
        raise RuntimeError("calendar is down")

    monkeypatch.setattr(prep.calendar, "events_between", boom)
    assert run_due(at + timedelta(seconds=1)) == 1
    assert [j for j in jobs_of("scan_meetings") if j.run_at > at]


def test_a_dead_chain_is_revived_by_the_recurring_pass(owner):
    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        preferences_for(s, person).digest = "morning"
        yesterday = datetime.now(UTC) - timedelta(days=1)
        job = digest.schedule(s, person, yesterday)
        assert job is not None
        job.status, job.finished_at = "failed", NOW  # exhausted its retries: nothing follows it
        scan = prep.schedule_scan(s, person, yesterday)
        assert scan is not None
        scan.status = "failed"
    assert not jobs_of("send_digest", "queued") and not jobs_of("scan_meetings", "queued")
    schedule_recurring()
    assert jobs_of("send_digest", "queued") and jobs_of("scan_meetings", "queued")


def test_the_worker_runs_the_recurring_pass_on_a_timer_not_only_at_start():
    worker = JobWorker(recurring_every=300)
    worker._last_recurring = 1000.0
    assert worker.recurring_if_due(now=1100.0) is False
    assert worker.recurring_if_due(now=1301.0) is True
    assert worker.recurring_if_due(now=1400.0) is False


def test_a_new_hire_gets_a_meeting_scan_chain_without_a_worker_restart(owner):
    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        from superaide.app.onboarding import schedule_for_new_hire

        schedule_for_new_hire(s, person)
    assert jobs_of("send_digest", "queued") and jobs_of("scan_meetings", "queued")


# --- BE-07: a reminder whose delivery job died is brought back -------------------------------


def test_a_reminder_whose_job_failed_is_requeued_and_delivered(owner):
    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        item = intentions.create(s, person, text="Renew passport", due_at=datetime.now(UTC) + timedelta(minutes=5))
        item_id = item.id
    [job] = jobs_of("deliver_intention")
    with session_scope() as s:
        s.get(Job, job.id).status = "failed"  # type: ignore[union-attr]
    assert not jobs_of("deliver_intention", "queued")
    schedule_recurring()  # the reconciler runs in every instance's scope
    assert len(jobs_of("deliver_intention", "queued")) == 1
    assert run_due(datetime.now(UTC) + timedelta(minutes=6)) >= 1
    with session_scope() as s:
        assert s.get(Intention, item_id).delivered_at is not None  # type: ignore[union-attr]


def test_a_reminder_with_no_job_at_all_gets_one(owner):
    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        intentions.create(s, person, text="Call the bank", due_at=datetime.now(UTC) + timedelta(hours=1))
        for j in s.scalars(select(Job).where(Job.kind == "deliver_intention")):
            s.delete(j)
    schedule_recurring()
    assert len(jobs_of("deliver_intention", "queued")) == 1


# --- BE-08: stale claims, shutdown, poison jobs -----------------------------------------------


@pytest.fixture
def ran(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(queue, "HANDLERS", {})

    @handler("ok")
    def ok(session, job):
        calls.append(str(job.payload.get("n")))

    return calls


def test_a_job_that_keeps_crashing_the_worker_is_failed_not_rerun_forever(ran):
    with session_scope() as s:
        j = enqueue(s, "ok", {"n": 1}, run_at=NOW - timedelta(hours=2), max_attempts=3)
        assert j is not None
        j.status, j.locked_at, j.attempts = "running", NOW - timedelta(minutes=30), 3  # every attempt was spent
    assert run_due(NOW) == 1
    assert ran == []
    [row] = jobs_of("ok")
    assert row.status == "failed" and "attempts" in (row.last_error or "")


def test_a_stop_between_jobs_gives_back_the_claims_that_never_started(ran):
    with session_scope() as s:
        for n in range(3):
            enqueue(s, "ok", {"n": n}, run_at=NOW - timedelta(minutes=3 - n))
    stop = threading.Event()

    @handler("ok")
    def first_then_stop(session, job):
        ran.append(str(job.payload.get("n")))
        stop.set()  # a shutdown arrives while the first job runs

    run_due(NOW, stop=stop)
    # Which job is claimed first is not what this test is about (it varies when the database is busy):
    # exactly one ran, and the claims that never started went back at once.
    assert len(ran) == 1
    first = int(ran[0])
    rows = {j.payload["n"]: j for j in jobs_of("ok")}
    assert rows[first].status == "done"
    for n in {0, 1, 2} - {first}:
        assert rows[n].status == "queued" and rows[n].attempts == 0  # not "running" for 10 more minutes
    assert run_due(NOW) == 2


def test_each_job_refreshes_its_lock_when_it_starts(ran, monkeypatch):
    with session_scope() as s:
        enqueue(s, "ok", {"n": 1}, run_at=NOW - timedelta(minutes=1))
    seen: list[datetime | None] = []

    @handler("ok")
    def look(session, job):
        seen.append(job.locked_at)

    monkeypatch.setattr(queue, "_now", lambda: NOW + timedelta(minutes=8))  # a long batch: this job starts late
    run_due(NOW)  # claimed at NOW...
    assert seen == [NOW + timedelta(minutes=8)]  # ...and its lock restarted when it began


# --- BE-16 / BE-17: the ledger stays honest -----------------------------------------------------


def test_a_call_cut_off_after_the_stream_began_is_billed_at_its_reservation():
    guard = BudgetGuard(10)

    def cut_off() -> str:
        err = ConnectionError("stream dropped")
        raise mark_billable(err)

    with pytest.raises(ConnectionError):
        metered(guard, purpose="chat", model="claude-haiku-5-5", worst_case_usd=Decimal("0.02"), call=cut_off,
                priced=lambda r: Priced(UsageCounts()))  # fmt: skip
    summary = guard.summary()
    assert summary.settled_usd == Decimal("0.02") and summary.reserved_usd == 0


def test_a_call_that_failed_before_any_response_is_still_free():
    guard = BudgetGuard(10)

    def refused() -> str:
        raise ConnectionError("could not connect")

    with pytest.raises(ConnectionError):
        metered(guard, purpose="chat", model="claude-haiku-5-5", worst_case_usd=Decimal("0.02"), call=refused,
                priced=lambda r: Priced(UsageCounts()))  # fmt: skip
    assert guard.summary().settled_usd == 0


def test_a_reservation_orphaned_by_a_crash_is_released_by_the_next_reserve():
    guard = BudgetGuard(1)
    old = guard.reserve(purpose="chat", model="claude-haiku-5-5", worst_case_usd=Decimal("0.4"))
    fresh = guard.reserve(purpose="chat", model="claude-haiku-5-5", worst_case_usd=Decimal("0.1"))
    with session_scope() as s:
        s.get(SpendEntry, old).created_at = datetime.now(UTC) - timedelta(hours=1)  # type: ignore[union-attr]
    guard.reserve(purpose="chat", model="claude-haiku-5-5", worst_case_usd=Decimal("0.1"))
    summary = guard.summary()
    assert summary.reserved_usd == Decimal("0.2")  # the stale 0.4 is gone; the fresh ones stay
    with session_scope() as s:
        assert s.get(SpendEntry, old).status == "failed"  # type: ignore[union-attr]
        assert s.get(SpendEntry, fresh).status == "reserved"  # type: ignore[union-attr]


def test_an_orphaned_platform_reservation_gives_back_the_platform_allowance(monkeypatch):
    from superaide.config import get_settings

    monkeypatch.setattr(get_settings(), "platform_daily_cap_usd", 5.0)
    monkeypatch.setattr(get_settings(), "platform_owner_daily_usd", 0)
    monkeypatch.setattr(get_settings(), "platform_owner_monthly_usd", 0)
    # Everything reserved so far counts as stale. (Aging the row by an hour moved it into yesterday just after
    # midnight UTC, and the platform allowance is given back to the day the reservation was made.)
    monkeypatch.setattr("superaide.brain.budget.STALE_RESERVATION", timedelta(seconds=-1))
    guard = BudgetGuard(10, key_source="platform")
    old = guard.reserve(purpose="chat", model="claude-haiku-5-5", worst_case_usd=Decimal("1"))
    with session_scope() as s:
        assert platform_spend.committed_today(s) == Decimal("1")
        assert s.get(SpendEntry, old).status == "reserved"  # type: ignore[union-attr]
    guard.reserve(purpose="chat", model="claude-haiku-5-5", worst_case_usd=Decimal("0.5"))
    with session_scope() as s:
        assert platform_spend.committed_today(s) == Decimal("0.5")


# --- BE-15: a stalled provider cannot hold a worker for ten minutes -----------------------------


def test_the_anthropic_client_has_an_explicit_timeout(monkeypatch):
    import anthropic

    from superaide.brain.providers.anthropic import AnthropicProvider

    seen: dict = {}

    class Fake:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    AnthropicProvider(BudgetGuard(10), api_key="sk-test")
    assert 0 < float(seen["timeout"]) <= 180


def test_a_stream_that_dies_midway_marks_the_error_billable():
    from superaide.brain.providers.anthropic import AnthropicProvider

    class Events:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            yield type("E", (), {"type": "message_start"})()
            raise ConnectionError("reset by peer")

    client = type("C", (), {"messages": type("M", (), {"stream": staticmethod(lambda **k: Events())})()})()
    provider = AnthropicProvider(BudgetGuard(10), client=client)
    with pytest.raises(ConnectionError) as info:
        provider._stream({}, lambda kind, data: None)
    assert getattr(info.value, "billable", False) is True


# --- #84: concurrent turns can't starve the pool ------------------------------------------------


def test_the_engine_pool_comes_from_settings():
    from superaide.db.session import get_engine

    pool = get_engine().pool
    assert pool.size() >= 10 and pool._max_overflow >= 10  # type: ignore[attr-defined]
    assert pool._timeout > 0  # type: ignore[attr-defined]


def test_turns_are_capped_below_the_pool_so_each_can_get_its_second_connection(agent, monkeypatch):
    from superaide.channels.base import InboundMessage
    from superaide.pipeline import max_concurrent_turns

    cap = max_concurrent_turns()
    from superaide.db.session import get_engine

    pool = get_engine().pool
    assert 1 <= cap < pool.size() + pool._max_overflow  # type: ignore[attr-defined]

    running = peak = 0
    lock = threading.Lock()

    def slow(self, msg, emit=None):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1

    from superaide.pipeline import Agent

    monkeypatch.setattr(Agent, "_handle", slow)
    msg = InboundMessage(
        provider="web", tenant_scope="", sender_id="u", chat_id="c", chat_kind="dm", text="hi", verified=True
    )
    threads = [threading.Thread(target=agent.handle, args=(msg,)) for _ in range(cap * 3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert peak <= cap
