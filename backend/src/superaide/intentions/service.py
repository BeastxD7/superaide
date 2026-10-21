"""Reminders: create, deliver on time, snooze, finish.

Delivery goes to the owner's in-app chat always, and also to the reminder's channel
(Telegram or Slack) when the owner linked it; "default" means Telegram when the owner
chose it for interruptions. A reminder is delivered by a job keyed on
its id and due time, so rescheduling never sends it twice.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.channels import health
from superaide.db.models import Intention, Job, Person
from superaide.intentions.when import zone
from superaide.jobs.queue import cancel, enqueue, handler
from superaide.knowledge.store import preferences_for
from superaide.proactive.notify import CHANNEL_NAMES, CHANNELS, EXTERNAL, identity_for, notify_owner

log = logging.getLogger(__name__)

JOB_KIND = "deliver_intention"
MAX_TEXT = 500


class IntentionError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _job_key(intention: Intention) -> str:
    return f"intention:{intention.id}:{int(intention.due_at.timestamp())}"


def _schedule(session: Session, intention: Intention) -> None:
    payload = {"intention_id": str(intention.id)}
    key = _job_key(intention)
    if enqueue(session, JOB_KIND, payload, run_at=intention.due_at, dedupe_key=key) is not None:
        return
    # Moved back to a time it had before: that job was cancelled, so bring it back.
    job = session.scalar(select(Job).where(Job.dedupe_key == key))
    if job is not None and job.status == "cancelled":
        job.status, job.run_at, job.attempts = "queued", intention.due_at, 0
        job.finished_at = job.locked_at = None
        job.last_error = None


def owner_zone(session: Session, owner: Person) -> str:
    return preferences_for(session, owner).timezone or "UTC"


def describe_due(due: datetime, tz: str, now: datetime | None = None) -> str:
    """ "today at 5:00 pm", "tomorrow at 9:00 am", "Fri 24 Oct at 3:00 pm", in the owner's zone."""
    local = due.astimezone(zone(tz))
    today = (now or datetime.now(UTC)).astimezone(zone(tz)).date()
    clock = local.strftime("%I:%M %p").lstrip("0").lower()
    if local.date() == today:
        return f"today at {clock}"
    if local.date() == today + timedelta(days=1):
        return f"tomorrow at {clock}"
    return f"{local.strftime('%a')} {local.day} {local.strftime('%b')} at {clock}"


def create(
    session: Session,
    owner: Person,
    *,
    text: str,
    due_at: datetime,
    kind: str = "reminder",
    source: str = "chat",
    source_event_id: uuid.UUID | None = None,
    channel: str = "default",
) -> Intention:
    channel = check_channel(session, owner, channel)
    text = " ".join(text.split())[:MAX_TEXT]
    if not text:
        raise IntentionError(422, "What should I remind you about?")
    if due_at.tzinfo is None:
        raise IntentionError(422, "The time needs a time zone.")
    if due_at <= datetime.now(UTC) - timedelta(minutes=1):
        raise IntentionError(422, "That time has already passed.")
    intention = Intention(
        owner_person_id=owner.id,
        kind=kind if kind in {"reminder", "follow_up"} else "reminder",
        text=text,
        due_at=due_at.astimezone(UTC),
        timezone=owner_zone(session, owner),
        source=source,
        source_event_id=source_event_id,
        channel=channel,
    )
    session.add(intention)
    session.flush()
    _schedule(session, intention)
    audit(session, "intentions", "intention_created", intention_id=intention.id, kind=intention.kind, source=source)
    return intention


def check_channel(session: Session, owner: Person, channel: str | None) -> str:
    """A delivery channel the owner can actually receive on, or an error that says how to fix it."""
    channel = (channel or "default").strip().lower()
    if channel not in CHANNELS:
        raise IntentionError(422, f"Reminders can go to {', '.join(CHANNELS)}, not {channel!r}.")
    if channel in EXTERNAL and identity_for(session, owner, channel) is None:
        name = CHANNEL_NAMES[channel]
        raise IntentionError(
            409,
            f"{name} isn't linked, so a reminder can't be sent there. Tell the owner, and that they can link "
            f"{name} under Connections; or offer to set it in the app.",
        )
    if channel in EXTERNAL and health.rejected(session, channel):
        name = CHANNEL_NAMES[channel]
        token = "bot token" if channel == "telegram" else "token"
        raise IntentionError(
            409,
            f"{name} is linked, but its {token} was rejected, so nothing can be sent there right now. Tell the "
            f"owner to reconnect {name} under Connections; or offer to set the reminder in the app.",
        )
    return channel


def describe_channel(session: Session, owner: Person, channel: str) -> str:
    """Where a reminder will actually arrive, as of now: "in the app and on Telegram"."""
    if channel == "default":
        prefers = preferences_for(session, owner).interrupt_channel == "telegram"
        channel = "telegram" if prefers else "app"
    if channel in EXTERNAL and identity_for(session, owner, channel) is not None:
        if health.rejected(session, channel):
            return (
                f"in the app only ({CHANNEL_NAMES[channel]}'s token was rejected; it needs reconnecting under "
                "Connections)"
            )
        return f"in the app and on {CHANNEL_NAMES[channel]}"
    return "in the app"


def _owned(session: Session, owner: Person, intention_id: uuid.UUID) -> Intention:
    intention = session.get(Intention, intention_id)
    if intention is None or intention.owner_person_id != owner.id:
        raise IntentionError(404, "No such reminder")
    return intention


def finish(session: Session, owner: Person, intention_id: uuid.UUID, *, status: str) -> Intention:
    intention = _owned(session, owner, intention_id)
    if intention.status != "open":
        raise IntentionError(409, f"This reminder is already {intention.status}.")
    intention.status = status
    intention.closed_at = datetime.now(UTC)
    cancel(session, _job_key(intention))
    audit(session, "intentions", f"intention_{status}", intention_id=intention.id)
    return intention


def snooze(session: Session, owner: Person, intention_id: uuid.UUID, *, minutes: int) -> Intention:
    if not 1 <= minutes <= 60 * 24 * 30:
        raise IntentionError(422, "Snooze for between a minute and 30 days.")
    intention = _owned(session, owner, intention_id)
    if intention.status != "open":
        raise IntentionError(409, f"This reminder is already {intention.status}.")
    cancel(session, _job_key(intention))
    intention.due_at = datetime.now(UTC) + timedelta(minutes=minutes)
    intention.delivered_at = None
    intention.delivered_via = []
    _schedule(session, intention)
    audit(session, "intentions", "intention_snoozed", intention_id=intention.id, minutes=minutes)
    return intention


def change(
    session: Session,
    owner: Person,
    intention_id: uuid.UUID,
    *,
    due_at: datetime | None = None,
    text: str | None = None,
    channel: str | None = None,
) -> Intention:
    """Move an open reminder, reword it and/or change where it goes. Moving reschedules its delivery."""
    if due_at is None and text is None and channel is None:
        raise IntentionError(422, "Say what to change: the time, the task, the channel, or more than one.")
    intention = _owned(session, owner, intention_id)
    if intention.status != "open":
        raise IntentionError(409, f"This reminder is already {intention.status}.")
    # Check everything before changing anything, so a refused change leaves it as it was.
    if channel is not None:
        channel = check_channel(session, owner, channel)
    if text is not None:
        text = " ".join(text.split())[:MAX_TEXT]
        if not text:
            raise IntentionError(422, "What should I remind you about?")
    if due_at is not None:
        if due_at.tzinfo is None:
            raise IntentionError(422, "The time needs a time zone.")
        if due_at <= datetime.now(UTC) - timedelta(minutes=1):
            raise IntentionError(422, "That time has already passed.")
        due_at = due_at.astimezone(UTC)
    if channel is not None:
        intention.channel = channel
    if text is not None:
        intention.text = text
    if due_at is not None and due_at != intention.due_at:
        cancel(session, _job_key(intention))
        intention.due_at = due_at
        intention.delivered_at = None
        intention.delivered_via = []
        _schedule(session, intention)
    audit(
        session, "intentions", "intention_changed", intention_id=intention.id, moved=due_at is not None,
        reworded=text is not None, channel=intention.channel,
    )  # fmt: skip
    return intention


def upcoming(session: Session, owner: Person, *, include_closed: bool = False, limit: int = 100) -> list[Intention]:
    q = select(Intention).where(Intention.owner_person_id == owner.id)
    if not include_closed:
        q = q.where(Intention.status == "open")
    return list(session.scalars(q.order_by(Intention.due_at).limit(limit)))


# --- delivery ----------------------------------------------------------------------


@handler(JOB_KIND)
def deliver(session: Session, job: Job) -> None:
    intention = session.get(Intention, uuid.UUID(job.payload["intention_id"]))
    if intention is None or intention.status != "open" or intention.delivered_at is not None:
        return  # done, cancelled, or already sent: nothing to do
    owner = session.get(Person, intention.owner_person_id)
    if owner is None:
        return
    text = f"⏰ Reminder: {intention.text}"
    meta = {"intention_id": str(intention.id), "kind": "reminder"}
    via = notify_owner(session, owner, text, meta, channel=intention.channel or "default")
    intention.delivered_at = datetime.now(UTC)
    intention.delivered_via = via
    audit(session, "intentions", "intention_delivered", intention_id=intention.id, via=via)
