"""The commitments ledger (#480): create, move, close, remind, escalate. Decisions are code; the model writes none.

Where a row comes from:
- `from_relay`: the owner approved a message to someone ("tell Meera I'll reply in an hour"). The due time is read in
  code from the words the OWNER typed (`intentions/when.py`), anchored on when she approved, and only when those words
  promise a reply. No time, or no promise: no row. The model's draft text is never read for a time.
- `create`: the owner adds one in the app.

How it ends: the owner marks it done or cancelled, or writes to that person herself (`close_for_context`). Closing
cancels its reminders. Nothing is deleted: `remove` sets `removed_at`.

Reminders are three jobs per row, each sent once (dedupe key = id, phase, time) and each re-checking the state when it
fires, so nothing goes out after a reply: `nudge` shortly before it is due, `overdue` at the due time (a state change,
no message), `escalate` half an hour later (one more message). They go through `notify_owner`: in quiet hours, or when
the owner's interruption budget is used up, they stay in the app and nothing is pushed to Telegram or Slack."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.commitments.models import ACTIVE, Commitment
from superaide.identity.models import Person
from superaide.intentions.service import describe_due, owner_zone
from superaide.intentions.when import find_moment, zone
from superaide.jobs.models import Job
from superaide.jobs.queue import cancel, enqueue, handler
from superaide.memory.knowledge.profile import in_quiet_hours
from superaide.memory.knowledge.store import preferences_for
from superaide.platform.audit import audit
from superaide.proactive.models import NotificationDelivery
from superaide.proactive.notify import notify_owner

JOB_KIND = "commitment_step"
MAX_TEXT = 300
MAX_LABEL = 60
ESCALATE_AFTER = timedelta(minutes=30)
MIN_WINDOW = timedelta(minutes=4)  # a promise due sooner than this gets no early reminder
SHORT_WINDOW = timedelta(minutes=15)
MIN_LEAD = timedelta(minutes=5)

# The owner's words promise a reply, not just mention a time ("I'm in a meeting at 3" is not a commitment).
_PROMISE = re.compile(
    r"\b(?:i'?ll|i\s+will|i\s+shall|he'?ll|she'?ll|will)\b[^.!?]{0,60}\b(?:reply|respond|get\s+back|call|ring|"
    r"message|text|write\s+back|answer|be\s+in\s+touch|come\s+back|revert)\b",
    re.IGNORECASE,
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class CommitmentError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def plain(text: str, limit: int) -> str:
    return " ".join(_CONTROL.sub(" ", str(text or "")).split())[:limit]


def _key(c: Commitment, phase: str, now: datetime) -> str:
    """Unique per booking (the time it was booked is in it), so moving a promise back to an earlier time books anew."""
    return f"commitment:{c.id}:{phase}:{int(now.timestamp() * 1000)}"


def nudge_time(due: datetime, now: datetime) -> datetime | None:
    """When to remind before `due`: the last quarter hour mark that is at least five minutes ahead (due 15:08: 15:00),
    halfway for a promise under 15 minutes away, none under four minutes. Never in the past."""
    window = due - now
    if window < MIN_WINDOW:
        return None
    if window < SHORT_WINDOW:
        return now + window / 2
    mark = due.replace(minute=due.minute // 15 * 15, second=0, microsecond=0)
    if due - mark < MIN_LEAD:
        mark -= timedelta(minutes=15)
    if mark <= now + timedelta(minutes=1):
        mark = due - MIN_LEAD
    return mark


def _cancel_jobs(session: Session, c: Commitment) -> None:
    jobs = session.scalars(select(Job).where(Job.dedupe_key.like(f"commitment:{c.id}:%"), Job.status == "queued")).all()
    for job in jobs:
        if job.dedupe_key:
            cancel(session, job.dedupe_key)


def _schedule(session: Session, c: Commitment, now: datetime) -> None:
    """Book the reminders of an open row (once each; moving the time books new ones for the new time)."""
    _cancel_jobs(session, c)
    if c.state not in ("open", "overdue"):
        return
    payload = {"commitment_id": str(c.id)}
    if c.state == "open":
        if (at := nudge_time(c.due_at, now)) is not None:
            enqueue(session, JOB_KIND, {**payload, "phase": "nudge"}, run_at=at, dedupe_key=_key(c, "nudge", now))
        enqueue(session, JOB_KIND, {**payload, "phase": "overdue"}, run_at=c.due_at, dedupe_key=_key(c, "overdue", now))
    enqueue(
        session, JOB_KIND, {**payload, "phase": "escalate"},
        run_at=c.due_at + ESCALATE_AFTER, dedupe_key=_key(c, "escalate", now),
    )  # fmt: skip


def parse_promise(typed: str, now: datetime, tz: str) -> tuple[datetime, str] | None:
    """The moment the owner's own words promise a reply by, or None. `now` is when she approved the message."""
    typed = plain(typed, 400)
    if not typed or not _PROMISE.search(typed):
        return None
    found = find_moment(typed, now.astimezone(zone(tz)))
    if found is None:
        return None
    due = found[0].astimezone(UTC)
    return (due, typed) if due > now else None


def _first_name(label: str) -> str:
    word = ((label or "").split() or [""])[0]
    return "".join(ch for ch in word if ch.isalpha() or ch in "'-")[:20].strip("'-")


def from_relay(
    session: Session, owner: Person, *, approval_id: uuid.UUID, identity_id: uuid.UUID, context_id: uuid.UUID,
    provider: str, recipient: str, instruction: str, now: datetime | None = None,
) -> Commitment | None:  # fmt: skip
    """A relayed message that promised a reply creates one open commitment, once per approval."""
    now = now or datetime.now(UTC)
    existing = session.scalar(select(Commitment).where(Commitment.source_approval_id == approval_id))
    if existing is not None:
        return existing
    found = parse_promise(instruction, now, owner_zone(session, owner))
    if found is None:
        return None
    due, typed = found
    c = Commitment(
        owner_person_id=owner.id, debtor="owner", creditor_identity_id=identity_id, context_id=context_id,
        provider=provider, creditor_label=_first_name(recipient), text=typed[:MAX_TEXT], due_at=due, state="open",
        source="relay", source_approval_id=approval_id,
    )  # fmt: skip
    session.add(c)
    session.flush()
    _schedule(session, c, now)
    audit(session, "commitments", "created", commitment_id=c.id, source="relay", due_at=due.isoformat())
    return c


def create(
    session: Session, owner: Person, *, text: str, due_at: datetime, who: str = "", debtor: str = "owner",
    now: datetime | None = None,
) -> Commitment:  # fmt: skip
    now = now or datetime.now(UTC)
    body = plain(text, MAX_TEXT)
    if not body:
        raise CommitmentError(422, "What was promised?")
    if debtor not in ("owner", "ava"):
        raise CommitmentError(422, "Who owes it: you or Ava?")
    if due_at.tzinfo is None:
        raise CommitmentError(422, "The time needs a time zone.")
    if due_at <= now - timedelta(minutes=1):
        raise CommitmentError(422, "That time has already passed.")
    c = Commitment(
        owner_person_id=owner.id, debtor=debtor, creditor_label=plain(who, MAX_LABEL), text=body,
        due_at=due_at.astimezone(UTC), state="open", source="owner",
    )  # fmt: skip
    session.add(c)
    session.flush()
    _schedule(session, c, now)
    audit(session, "commitments", "created", commitment_id=c.id, source="owner", due_at=c.due_at.isoformat())
    return c


def _owned(session: Session, owner: Person, commitment_id: uuid.UUID) -> Commitment:
    c = session.get(Commitment, commitment_id)
    if c is None or c.owner_person_id != owner.id or c.removed_at is not None:
        raise CommitmentError(404, "No such promise")
    return c


def change(
    session: Session, owner: Person, commitment_id: uuid.UUID, *, text: str | None = None,
    due_at: datetime | None = None, who: str | None = None, now: datetime | None = None,
) -> Commitment:  # fmt: skip
    """Reword it, move its time, or rename the person. Moving an overdue one into the future reopens it."""
    now = now or datetime.now(UTC)
    if text is None and due_at is None and who is None:
        raise CommitmentError(422, "Say what to change: the words, the time, or who it is for.")
    c = _owned(session, owner, commitment_id)
    if c.state not in ACTIVE:
        raise CommitmentError(409, f"This promise is already {c.state}.")
    body = None
    if text is not None:
        body = plain(text, MAX_TEXT)
        if not body:
            raise CommitmentError(422, "What was promised?")
    if due_at is not None:
        if due_at.tzinfo is None:
            raise CommitmentError(422, "The time needs a time zone.")
        if due_at <= now - timedelta(minutes=1):
            raise CommitmentError(422, "That time has already passed.")
    if body is not None:
        c.text = body
    if who is not None:
        c.creditor_label = plain(who, MAX_LABEL)
    if due_at is not None and due_at.astimezone(UTC) != c.due_at:
        c.due_at = due_at.astimezone(UTC)
        c.state, c.nudged_at, c.overdue_at, c.escalated_at = "open", None, None, None
        _schedule(session, c, now)
    audit(session, "commitments", "changed", commitment_id=c.id, moved=due_at is not None, reworded=text is not None)
    return c


def close(
    session: Session, owner: Person, commitment_id: uuid.UUID, *, state: str = "done", by: str = "owner",
    now: datetime | None = None,
) -> Commitment:  # fmt: skip
    if state not in ("done", "cancelled"):
        raise CommitmentError(422, "A promise is closed as done or cancelled.")
    c = _owned(session, owner, commitment_id)
    if c.state not in ACTIVE:
        raise CommitmentError(409, f"This promise is already {c.state}.")
    _finish(session, c, state, by, now or datetime.now(UTC))
    return c


def _finish(session: Session, c: Commitment, state: str, by: str, now: datetime) -> None:
    _cancel_jobs(session, c)
    c.state, c.closed_at, c.closed_by = state, now, by
    audit(session, "commitments", f"commitment_{state}", commitment_id=c.id, by=by)


def close_for_context(session: Session, owner: Person, context_id: uuid.UUID, now: datetime | None = None) -> int:
    """The owner wrote to that person herself: the promises to them are kept. Returns how many were closed."""
    now = now or datetime.now(UTC)
    rows = session.scalars(
        select(Commitment).where(
            Commitment.owner_person_id == owner.id,
            Commitment.context_id == context_id,
            Commitment.debtor == "owner",
            Commitment.state.in_(ACTIVE),
            Commitment.removed_at.is_(None),
        )
    ).all()
    for c in rows:
        _finish(session, c, "done", "reply", now)
    return len(rows)


def remove(session: Session, owner: Person, commitment_id: uuid.UUID, now: datetime | None = None) -> Commitment:
    """Hide it from the owner's list. The row stays (retention keeps everything); an active one is cancelled first."""
    now = now or datetime.now(UTC)
    c = _owned(session, owner, commitment_id)
    if c.state in ACTIVE:
        _finish(session, c, "cancelled", "owner", now)
    c.removed_at = now
    audit(session, "commitments", "commitment_removed", commitment_id=c.id)
    return c


def listing(owner_id: uuid.UUID, *, closed: bool = False) -> Any:
    """The select behind the list endpoint (the caller pages it)."""
    states = ("done", "cancelled") if closed else ACTIVE
    return select(Commitment).where(
        Commitment.owner_person_id == owner_id, Commitment.state.in_(states), Commitment.removed_at.is_(None)
    )


def overdue(session: Session, owner: Person, now: datetime) -> list[Commitment]:
    return list(
        session.scalars(
            select(Commitment)
            .where(
                Commitment.owner_person_id == owner.id,
                Commitment.state.in_(("overdue", "escalated")),
                Commitment.removed_at.is_(None),
                Commitment.due_at <= now,
            )
            .order_by(Commitment.due_at)
        )
    )


def who_of(c: Commitment) -> str:
    return c.creditor_label or "someone"


def digest_lines(session: Session, owner: Person, now: datetime) -> list[str]:
    """Overdue promises, for the daily briefing: [] when there are none. Counts and the owner's own words only."""
    rows = overdue(session, owner, now)
    if not rows:
        return []
    tz = owner_zone(session, owner)
    out = ["**Promises past their time**"]
    for c in rows[:5]:
        out.append(f"- You told {who_of(c)} you'd get back by {describe_due(c.due_at, tz, now)}")
    if len(rows) > 5:
        out.append(f"- and {len(rows) - 5} more, under Promises")
    return out


# --- reminders -------------------------------------------------------------------------------


def _push_allowed(session: Session, owner: Person, now: datetime) -> bool:
    """Quiet hours and the interruption budget (the same hourly and daily limits triage uses) decide whether a
    reminder may also go to Telegram or Slack; otherwise it stays in the app."""
    from superaide.triage.store import settings_for

    if in_quiet_hours(preferences_for(session, owner), now):
        return False
    ts = settings_for(session, owner)

    def pushed(since: datetime) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(NotificationDelivery)
                .where(
                    NotificationDelivery.meta["kind"].astext == "commitment", NotificationDelivery.created_at >= since
                )
            )
            or 0
        )

    return pushed(now - timedelta(hours=1)) < ts.max_per_hour and pushed(now - timedelta(days=1)) < ts.max_per_day


def _line(session: Session, owner: Person, c: Commitment, phase: str, now: datetime) -> str:
    tz = owner_zone(session, owner)
    who = who_of(c)
    when = describe_due(c.due_at, tz, now)
    if c.debtor == "ava":
        return f"⏳ Ava promised {who} something {when}: “{c.text}”. Under Promises you can mark it done."
    if phase == "nudge":
        return f"⏳ You told {who} you'd get back to them {when}. Reply to them or mark it done under Promises."
    return f"⚠️ Overdue: you told {who} you'd get back to them by {when}. Reply to them or mark it done under Promises."


def step(session: Session, commitment_id: uuid.UUID, phase: str, now: datetime | None = None) -> str | None:
    """Run one reminder phase if the row is still in the state that phase is for. Returns what was done."""
    now = now or datetime.now(UTC)
    c = session.get(Commitment, commitment_id)
    if c is None or c.removed_at is not None:
        return None
    owner = session.get(Person, c.owner_person_id)
    if owner is None:
        return None
    if phase == "nudge":
        if c.state != "open" or c.nudged_at is not None or c.due_at <= now:
            return None
        c.nudged_at = now
    elif phase == "overdue":
        if c.state != "open":
            return None
        c.state, c.overdue_at = "overdue", now
        audit(session, "commitments", "commitment_overdue", commitment_id=c.id)
        return "overdue"
    elif phase == "escalate":
        if c.state not in ("open", "overdue") or c.escalated_at is not None:
            return None
        c.state, c.escalated_at = "escalated", now
    else:
        return None
    channel = "default" if _push_allowed(session, owner, now) else "app"
    meta = {"kind": "commitment", "commitment_id": str(c.id), "phase": phase}
    via = notify_owner(session, owner, _line(session, owner, c, phase, now), meta, channel=channel)
    audit(session, "commitments", f"commitment_{phase}", commitment_id=c.id, via=via)
    return phase


@handler(JOB_KIND)
def run_step(session: Session, job: Job) -> None:
    step(session, uuid.UUID(job.payload["commitment_id"]), str(job.payload.get("phase", "")))
