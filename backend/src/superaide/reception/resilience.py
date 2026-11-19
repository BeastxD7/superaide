"""Graceful degradation for people who write to Ava (issue #503).

Outages become a status the owner can read and a queue that drains, never a crash or a silence:

- The model, the budget or a channel fails: the visitor still gets fixed, honest text (reception/service.py:
  FALLBACK, PAUSED, SAVE_FAILED), and the message is stored before anything says "passed on".
- A reply that could not be sent to a visitor goes onto the job queue (`visitor_reply_retry`) with the same
  back-off as every job; when the channel heals it is sent once, and the stored reply is marked delivered late.
  After the last attempt it is marked as not delivered, so no record claims a delivery that did not happen.
- `status()` is the owner's chip and banner: since when, what is waiting, what is basic. Fixed text only, never
  a word a visitor wrote.

The ladder, decided in code: normal, then `basic` (the model or the budget is unavailable: fixed replies, every
message stored), then `paused` (the owner's kill switch: nothing is answered, nothing is stored as a message).
A cheaper-model rung and a reserved floor of budget for friends are not built yet (see the issue).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.channels import health
from superaide.jobs import queue as jobs
from superaide.jobs.models import Job
from superaide.memory.models import Context, Event
from superaide.platform.audit import audit
from superaide.policy.killswitch import get_state, is_frozen, set_state

log = logging.getLogger(__name__)

JOB = "visitor_reply_retry"
MAX_ATTEMPTS = 8  # with the queue's back-off (30 s, 1 min, 2, 4, 8, 16, 32): a channel that heals within an hour
MODEL_KEY = "model_health"
OK, BUDGET, ERROR, PAUSED = "ok", "budget", "error", "paused"
# A failure that is not seen again is forgotten after this long (every visitor message tests the model again).
EXPIRES = {ERROR: timedelta(minutes=30), BUDGET: timedelta(minutes=60)}
LISTED_FOR = timedelta(days=7)
CHANNEL_NAMES = {"telegram": "Telegram", "slack": "Slack"}


def record_model(session: Session, status: str, detail: str = "") -> None:
    """Remember how the last model call went. Writes only when something changed (or a failure repeats later than
    a minute ago, to keep it fresh). Best effort: a bookkeeping failure never breaks a turn."""
    try:
        now = datetime.now(UTC)
        cur = get_state(session, MODEL_KEY)
        if cur.get("status", OK) == status:
            if status == OK or now - datetime.fromisoformat(cur["last_at"]) < timedelta(minutes=1):
                return
        since = cur.get("since") if cur.get("status", OK) == status and cur.get("since") else now.isoformat()
        if status == OK:
            since = None
        with session.begin_nested():
            set_state(session, MODEL_KEY, {"status": status, "detail": detail[:60], "since": since,
                                           "last_at": now.isoformat()})  # fmt: skip
            if cur.get("status", OK) != status:
                audit(session, "reception", "model_health", status=status, detail=detail[:60])
    except Exception:
        log.warning("couldn't record model health", exc_info=True)


def model_trouble(session: Session, now: datetime | None = None) -> dict[str, Any] | None:
    """{"status", "detail", "since"} while the model or the budget is failing and the failure is recent, else None."""
    now = now or datetime.now(UTC)
    cur = get_state(session, MODEL_KEY)
    status = cur.get("status", OK)
    if status not in EXPIRES or not cur.get("last_at"):
        return None
    if now - datetime.fromisoformat(cur["last_at"]) > EXPIRES[status]:
        return None
    return {"status": status, "detail": cur.get("detail", ""), "since": cur.get("since")}


def _count_jobs(session: Session, *statuses: str, since: datetime | None = None) -> int:
    q = select(func.count()).select_from(Job).where(Job.kind == JOB, Job.status.in_(statuses))
    if since is not None:
        q = q.where(Job.finished_at >= since)
    return int(session.scalar(q) or 0)


def status(session: Session, now: datetime | None = None) -> dict[str, Any] | None:
    """What the owner sees: None while everything works, else a level, the reasons, the counts and one line."""
    from superaide.reception.models import ReceptionMessage

    now = now or datetime.now(UTC)
    reasons: list[str] = []
    level = "normal"
    since: str | None = None
    trouble = model_trouble(session, now)
    if is_frozen(session):
        level = "paused"
        reasons.append("paused")
    elif trouble is not None:
        level = "basic"
        since = trouble["since"]
        reasons.append("budget" if trouble["status"] == BUDGET else "model")
    channels: list[str] = []
    for provider in health.PROVIDERS:
        h = health.get(session, provider)
        if h["status"] != health.OK:
            channels.append(provider)
            since = since or h.get("since")
    queued = _count_jobs(session, "queued", "running")
    failed = _count_jobs(session, "failed", since=now - LISTED_FOR)
    if level == "normal" and not channels and not queued and not failed:
        return None
    waiting = 0
    if level == "basic" and since:
        waiting = int(
            session.scalar(
                select(func.count())
                .select_from(ReceptionMessage)
                .where(
                    ReceptionMessage.source == "code",
                    ReceptionMessage.status == "new",
                    ReceptionMessage.created_at >= datetime.fromisoformat(since),
                )
            )
            or 0
        )
    return {
        "level": level, "reasons": reasons, "channels": channels, "since": since, "waiting_messages": waiting,
        "queued_replies": queued, "failed_replies": failed,
        "line": describe(level, reasons, channels, since, waiting, queued, failed, _owner_zone(session)),
    }  # fmt: skip


def _owner_zone(session: Session) -> Any:
    """The owner's own time zone, so "since 14:02" is their clock."""
    from superaide.app.accounts import owner as hired_owner
    from superaide.intentions.when import zone
    from superaide.memory.knowledge.store import preferences_for

    boss = hired_owner(session)
    return zone(preferences_for(session, boss).timezone) if boss is not None else UTC


def describe(
    level: str,
    reasons: list[str],
    channels: list[str],
    since: str | None,
    waiting: int,
    queued: int,
    failed: int,
    tz: Any = UTC,
) -> str:
    """One sentence in fixed words: what is wrong, how long, and what to expect."""
    parts: list[str] = []
    when = f" since {datetime.fromisoformat(since).astimezone(tz).strftime('%H:%M')}" if since else ""
    if level == "paused":
        parts.append("Paused: nobody is answered and no new messages are taken until you resume")
    elif "budget" in reasons:
        parts.append(f"The spending limit is reached{when}; replies are basic and every message is still kept")
    elif "model" in reasons:
        parts.append(f"The AI model is unavailable{when}; replies are basic and every message is still kept")
    if channels:
        names = " and ".join(CHANNEL_NAMES[p] for p in channels)
        parts.append(f"{names} isn't accepting messages, so replies there are retried")
    if waiting:
        parts.append(f"{waiting} message{'s' if waiting != 1 else ''} waiting for you")
    if queued:
        parts.append(f"{queued} repl{'ies' if queued != 1 else 'y'} queued to send")
    if failed:
        parts.append(f"{failed} repl{'ies' if failed != 1 else 'y'} could not be delivered")
    return "; ".join(parts) + "."


# --- queued replies ---------------------------------------------------------------------------------


def queue_reply(session: Session, ctx: Context, event: Event, text: str, thread: str, kind: str) -> bool:
    """A reply that did not go out: queue it once (the event is the dedupe key). False when it could not be
    queued, which the caller leaves as it is: the reply is stored and marked undelivered either way."""
    try:
        with session.begin_nested():
            jobs.enqueue(
                session, JOB, {"context_id": str(ctx.id), "event_id": str(event.id), "text": text, "thread": thread,
                               "provider": ctx.provider, "kind": kind},
                run_at=datetime.now(UTC) + timedelta(seconds=30), dedupe_key=f"vreply:{event.id}",
                max_attempts=MAX_ATTEMPTS,
            )  # fmt: skip
        audit(session, "pipeline", "reply_queued", provider=ctx.provider, reason=kind)
        return True
    except Exception:
        log.exception("couldn't queue an undelivered reply")
        return False


def _mark(session: Session, event_id: uuid.UUID, **changes: Any) -> None:
    ev = session.get(Event, event_id)
    if ev is None:
        return
    meta = {k: v for k, v in dict(ev.meta or {}).items() if not (changes.get("delivered_late") and k == "undelivered")}
    ev.meta = {**meta, **changes}


@jobs.handler(JOB)
def _deliver(session: Session, job: Job) -> None:
    """Send the queued reply. Raising keeps it in the queue with back-off; on the last attempt the stored reply is
    marked as never delivered (in its own transaction, as this one rolls back)."""
    p = job.payload
    ctx = session.get(Context, uuid.UUID(p["context_id"]))
    event_id = uuid.UUID(p["event_id"])
    if ctx is None:
        return
    ev = session.get(Event, event_id)
    if ev is not None and (ev.meta or {}).get("delivered_late"):
        return  # delivered by an earlier attempt that did not get to record it
    provider = ctx.provider
    try:
        if health.rejected(session, provider):  # the circuit is open: the token is refused, don't hammer the service
            raise health.DeliveryFailed(provider, health.AUTH_FAILED, "token rejected")
        from superaide.proactive.notify import messenger_for

        messenger = messenger_for(provider)
        if messenger is None:
            raise health.DeliveryFailed(provider, health.UNREACHABLE, "not connected")
        if messenger.send_to_context(ctx, p["text"], p.get("thread", "")) is False:
            raise health.DeliveryFailed(provider, "refused", "the service refused the message")
    except Exception as exc:
        if job.attempts >= job.max_attempts:
            from superaide.platform.db.session import session_scope

            with session_scope() as s:
                _mark(s, event_id, undelivered_final=True)
                audit(s, "pipeline", "reply_given_up", provider=provider, reason=type(exc).__name__)
        raise
    _mark(session, event_id, delivered_late=True)
    audit(session, "pipeline", "reply_delivered_late", provider=provider, attempts=job.attempts)
