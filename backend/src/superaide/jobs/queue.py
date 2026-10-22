"""A small job queue in Postgres.

Jobs are rows. A worker claims due ones with FOR UPDATE SKIP LOCKED, runs the
handler registered for the job's kind, and records the outcome. Failures retry with
exponential back-off up to `max_attempts`. A job left "running" by a crashed worker
is picked up again after `STALE_AFTER`. Handlers must be safe to run twice.

Many owners (ADR 0009 T5): a job belongs to the agent instance that was in scope
when it was queued. Claiming is fair across instances (jobs/claim.py), and each job
runs inside its own instance's scope, so what a handler reads and writes is that
owner's. `dedupe_key` is unique per instance.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.brain import keyhealth
from superaide.db.models import Job
from superaide.db.session import session_scope
from superaide.tenancy.context import InstanceScope, scope_or_default, use_instance

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=10)
PER_INSTANCE = 5  # at most this many of one instance's jobs in one batch, so no owner crowds out the rest
Handler = Callable[[Session, Job], None]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    """Register the function that runs jobs of this kind."""

    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return register


def _now() -> datetime:
    return datetime.now(UTC)


def enqueue(
    session: Session,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
) -> Job | None:
    """Add a job. With a `dedupe_key` that already exists, nothing is added and None comes back."""
    values = {
        "kind": kind,
        "payload": payload or {},
        "run_at": run_at or _now(),
        "dedupe_key": dedupe_key,
        "max_attempts": max_attempts,
    }
    scope = scope_or_default()
    values.update(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
    stmt = (
        insert(Job).values(**values).on_conflict_do_nothing(index_elements=["agent_id", "dedupe_key"]).returning(Job.id)
    )
    job_id = session.scalar(stmt)
    return session.get(Job, job_id) if job_id else None


def cancel(session: Session, dedupe_key: str) -> bool:
    job = session.scalar(
        select(Job).where(
            Job.agent_id == scope_or_default().agent_id, Job.dedupe_key == dedupe_key, Job.status == "queued"
        )
    )
    if job is None:
        return False
    job.status = "cancelled"
    job.finished_at = _now()
    return True


def _claim(session: Session, now: datetime, limit: int) -> list[tuple[Any, str, InstanceScope]]:
    """Mark up to `limit` due jobs as running, fairly across instances: (id, kind, instance) each."""
    rows = session.execute(
        text("SELECT id, kind, tenant_id, agent_id FROM claim_due_jobs(:now, :limit, :per, :stale)"),
        {"now": now, "limit": limit, "per": PER_INSTANCE, "stale": STALE_AFTER},
    ).all()
    return [(r.id, r.kind, InstanceScope(tenant_id=r.tenant_id, agent_id=r.agent_id)) for r in rows]


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(30 * 2 ** (attempts - 1), 3600))


def run_due(now: datetime | None = None, limit: int = 20, stop: threading.Event | None = None) -> int:
    """Run every job that is due. Returns how many ran (succeeded or failed).

    When `stop` is set (a shutdown) between jobs, the claims that haven't started are given back at once,
    instead of staying "running" until they go stale."""
    now = now or _now()
    with session_scope() as s:
        claimed = _claim(s, now, limit)
    for done, (job_id, kind, scope) in enumerate(claimed):
        if stop is not None and stop.is_set():
            for rest_id, _kind, rest_scope in claimed[done:]:
                with use_instance(rest_scope):
                    _release(rest_id)
            return done
        with use_instance(scope):  # the job runs as its own owner's work
            _run_one(job_id, kind)
    return len(claimed)


def _release(job_id: Any) -> None:
    """Give a claimed job that never started back to the queue, as if it had not been claimed."""
    try:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is not None and job.status == "running":
                job.status, job.locked_at = "queued", None
                job.attempts = max(0, job.attempts - 1)
    except Exception:
        log.exception("couldn't give back job %s; it will be picked up when its claim goes stale", job_id)


def _start(job_id: Any, kind: str) -> bool:
    """Refresh the claim (a batch is claimed at once, so a late job's lock is old) and refuse a job whose
    attempts are spent: one that crashes the worker is reclaimed as stale, which counts as an attempt."""
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            log.info("job %s (%s) is gone; skipped", job_id, kind)
            return False
        if job.attempts > job.max_attempts:
            job.status, job.finished_at = "failed", _now()
            job.last_error = f"gave up: {job.max_attempts} attempts used, the worker stopped during each of them"
            audit(s, "jobs", "job_failed", job_id=job.id, kind=kind, error=job.last_error[:300])
            return False
        job.locked_at = _now()
    return True


def _run_one(job_id: Any, kind: str) -> None:
    """Run one claimed job (in its instance's scope) and record how it went."""
    fn = HANDLERS.get(kind)
    try:
        if fn is None:
            raise LookupError(f"no handler for job kind {kind!r}")
        if not _start(job_id, kind):
            return
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:  # deleted since it was claimed (a database reset): nothing to run
                log.info("job %s (%s) is gone; skipped", job_id, kind)
                return
            fn(s, job)
            job.status = "done"
            job.finished_at = _now()
            job.last_error = None
    except Exception as exc:  # one job failing never stops the others
        log.exception("job %s (%s) failed", job_id, kind)
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            job.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            if job.attempts >= job.max_attempts or keyhealth.is_auth_error(exc):  # a rejected key won't heal
                job.status = "failed"
                job.finished_at = _now()
                audit(s, "jobs", "job_failed", job_id=job.id, kind=kind, error=job.last_error[:300])
            else:
                job.status = "queued"
                job.run_at = _now() + _backoff(job.attempts)
