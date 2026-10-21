"""A small job queue in Postgres.

Jobs are rows. A worker claims due ones with FOR UPDATE SKIP LOCKED, runs the
handler registered for the job's kind, and records the outcome. Failures retry with
exponential back-off up to `max_attempts`. A job left "running" by a crashed worker
is picked up again after `STALE_AFTER`. Handlers must be safe to run twice.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.db.models import Job
from superaide.db.session import session_scope

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=10)
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
    stmt = insert(Job).values(**values).on_conflict_do_nothing(index_elements=["dedupe_key"]).returning(Job.id)
    job_id = session.scalar(stmt)
    return session.get(Job, job_id) if job_id else None


def cancel(session: Session, dedupe_key: str) -> bool:
    job = session.scalar(select(Job).where(Job.dedupe_key == dedupe_key, Job.status == "queued"))
    if job is None:
        return False
    job.status = "cancelled"
    job.finished_at = _now()
    return True


def _claim(session: Session, now: datetime, limit: int) -> list[Job]:
    due = (
        select(Job)
        .where(
            or_(
                (Job.status == "queued") & (Job.run_at <= now),
                (Job.status == "running") & (Job.locked_at < now - STALE_AFTER),
            )
        )
        .order_by(Job.run_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    jobs = list(session.scalars(due))
    for job in jobs:
        job.status = "running"
        job.locked_at = now
        job.attempts += 1
    return jobs


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(30 * 2 ** (attempts - 1), 3600))


def run_due(now: datetime | None = None, limit: int = 20) -> int:
    """Run every job that is due. Returns how many ran (succeeded or failed)."""
    now = now or _now()
    with session_scope() as s:
        claimed = [(j.id, j.kind) for j in _claim(s, now, limit)]
    for job_id, kind in claimed:
        fn = HANDLERS.get(kind)
        try:
            if fn is None:
                raise LookupError(f"no handler for job kind {kind!r}")
            with session_scope() as s:
                job = s.get(Job, job_id)
                assert job is not None
                fn(s, job)
                job.status = "done"
                job.finished_at = _now()
                job.last_error = None
        except Exception as exc:  # one job failing never stops the others
            log.exception("job %s (%s) failed", job_id, kind)
            with session_scope() as s:
                job = s.get(Job, job_id)
                assert job is not None
                job.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                if job.attempts >= job.max_attempts:
                    job.status = "failed"
                    job.finished_at = _now()
                    audit(s, "jobs", "job_failed", job_id=job.id, kind=kind, error=job.last_error[:300])
                else:
                    job.status = "queued"
                    job.run_at = _now() + _backoff(job.attempts)
    return len(claimed)
