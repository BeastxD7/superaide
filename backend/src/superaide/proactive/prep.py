"""Pre-meeting prep: about 15 minutes before a calendar event, what Ava knows about it.

Code only, no model call. Meetings get a prep job from the daily digest (the day's events)
and from a half-hourly scan of the next couple of hours, which catches events added later.
Each event start gets at most one job (dedupe key per event id and start time). Before
sending, the job asks Google for the event again: cancelled or declined means skip, moved
means re-queue for the new start; if Google can't be reached the stored copy is used. Then it
recalls the owner's notes and messages about the attendees and title; if there's nothing
useful, or it's inside the owner's quiet hours (unless a VIP contact is attending and the
owner lets VIPs through), nothing is sent.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.context.audience import Audience
from superaide.context.recall import Source, recall
from superaide.db.models import Contact, Job, Person
from superaide.db.session import require, session_scope
from superaide.intentions.service import owner_zone
from superaide.intentions.when import zone
from superaide.jobs.queue import enqueue, handler
from superaide.knowledge.profile import in_quiet_hours
from superaide.knowledge.store import preferences_for
from superaide.proactive.notify import notify_owner
from superaide.skills import calendar

log = logging.getLogger(__name__)
JOB_KIND = "meeting_prep"
SCAN_KIND = "scan_meetings"
LEAD = timedelta(minutes=15)
SCAN_EVERY = timedelta(minutes=30)
LOOKAHEAD = timedelta(hours=2)
MAX_ITEMS = 3
# Words in meeting titles that say nothing about who or what it is.
GENERIC = set(
    "and the for with from about meeting meet call sync catch chat weekly daily monthly standup stand review "
    "check intro invite update session discussion".split()
)


def start_of(event: dict[str, Any]) -> datetime | None:
    """When a timed event starts; None for all-day events or bad data."""
    raw = (event.get("start") or {}).get("dateTime")
    try:
        return datetime.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


def attendee_names(event: dict[str, Any]) -> list[str]:
    """Other people at the event: their display name, else the email's local part."""
    names = []
    for a in event.get("attendees") or []:
        if a.get("self"):
            continue
        name = str(a.get("displayName") or str(a.get("email") or "").split("@")[0]).strip()
        if name:
            names.append(name[:80])
    return names[:10]


def _declined(event: dict[str, Any]) -> bool:
    return any(a.get("self") and a.get("responseStatus") == "declined" for a in event.get("attendees") or [])


def prep_key(owner: Person, event_id: str, start: datetime) -> str:
    return f"prep:{owner.id}:{event_id}:{start.astimezone(UTC).isoformat()}"


def schedule_preps(session: Session, owner: Person, events: list[dict[str, Any]], now: datetime) -> list[Job]:
    """Queue one prep job per upcoming timed event; events already queued are left alone."""
    jobs = []
    for event in events:
        start, event_id = start_of(event), str(event.get("id") or "")
        if start is None or not event_id or start <= now or event.get("status") == "cancelled" or _declined(event):
            continue
        payload = {
            "owner_id": str(owner.id),
            "event_id": event_id,
            "title": str(event.get("summary") or "")[:200],
            "start": start.isoformat(),
            "names": attendee_names(event),
        }
        job = enqueue(
            session,
            JOB_KIND,
            payload,
            run_at=max(now, start - LEAD).astimezone(UTC),
            dedupe_key=prep_key(owner, event_id, start),
        )
        if job is not None:
            jobs.append(job)
    return jobs


def schedule_scan(session: Session, owner: Person, now: datetime | None = None) -> Job | None:
    """Queue the next look at the calendar, on the next half-hour boundary."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    step = int(SCAN_EVERY.total_seconds())
    at = datetime.fromtimestamp((int(now.timestamp()) // step + 1) * step, UTC)
    key = f"meetings:{owner.id}:{at.isoformat()}"
    return enqueue(session, SCAN_KIND, {"owner_id": str(owner.id)}, run_at=at, dedupe_key=key)


def vip_attending(session: Session, owner: Person, names: list[str]) -> bool:
    """Whether any attendee matches one of the owner's VIP contacts (full or first name)."""
    vips = session.scalars(
        select(Person.display_name)
        .join(Contact, Contact.person_id == Person.id)
        .where(Contact.owner_person_id == owner.id, Contact.vip.is_(True), Contact.removed_at.is_(None))
    ).all()
    wanted = {" ".join(v.lower().split()) for v in vips if v} | {v.lower().split()[0] for v in vips if v and v.split()}
    for name in names:
        n = " ".join(re.sub(r"[._]+", " ", name).lower().split())
        if n and (n in wanted or n.split()[0] in wanted):
            return True
    return False


def _terms(title: str, names: list[str]) -> list[str]:
    words = re.findall(r"[^\W\d_]{3,}", " ".join(names) + " " + title)
    out: list[str] = []
    for w in words:
        if w.lower() not in GENERIC and w.lower() not in {o.lower() for o in out}:
            out.append(w)
    return out[:12]


def gather(session: Session, owner: Person, title: str, names: list[str]) -> list[Source]:
    """What the owner's own notes and messages say about this meeting (owner-only audience)."""
    terms = _terms(title, names)
    if not terms:
        return []
    audience = Audience(person_ids={owner.id}, min_trust=2)
    found = recall(session, audience, " or ".join(terms), limit=MAX_ITEMS * 3)
    # Ava's own outbound messages (earlier digests and preps) are not news to the owner.
    useful = [s for s in found if not (s.kind == "message" and s.title.startswith("Ava"))]
    return useful[:MAX_ITEMS]


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split()).rstrip(".")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def compose(title: str, names: list[str], start: datetime, now: datetime, tz: Any, found: list[Source]) -> str | None:
    """The brief, or None when there's nothing useful to say."""
    if not found:
        return None
    minutes = max(1, round((start - now).total_seconds() / 60))
    head = f"In {minutes} min: {title or 'your meeting'} ({start.astimezone(tz):%H:%M}"
    head += f", with {', '.join(names[:4])})." if names else ")."
    lines = [head]
    notes = [s for s in found if s.kind == "memory"]
    msgs = [s for s in found if s.kind == "message"]
    if notes:
        lines.append("From your notes:")
        lines += [f"- {_short(s.text)}" for s in notes]
    if msgs:
        lines.append("From your messages:")
        lines += [f"- {_short(s.text)}" for s in msgs]
    return "\n".join(lines)


@handler(JOB_KIND)
def send_prep(session: Session, job: Job) -> None:
    owner = session.get(Person, uuid.UUID(job.payload["owner_id"]))
    if owner is None:
        return
    event_id = str(job.payload.get("event_id", ""))
    now = max(datetime.now(UTC), job.run_at)
    start = datetime.fromisoformat(str(job.payload["start"]))
    title, names = str(job.payload.get("title", "")), [str(n) for n in job.payload.get("names") or []]

    def skip(reason: str) -> None:
        audit(session, "proactive", "meeting_prep_skipped", reason=reason, event_id=event_id)

    try:
        fresh = calendar.fresh_event(session, owner, event_id)
    except calendar.Unknown:
        pass  # can't check: go with what we stored
    else:
        if fresh is None or fresh.get("status") == "cancelled":
            return skip("cancelled")
        if _declined(fresh):
            return skip("declined")
        new_start = start_of(fresh)
        if new_start is None:
            return skip("now_all_day")
        if new_start != start:
            schedule_preps(session, owner, [fresh], now)
            return skip("moved")
        title, names = str(fresh.get("summary") or "")[:200], attendee_names(fresh)
    if start <= now:
        return skip("started")
    pref = preferences_for(session, owner)
    if in_quiet_hours(pref, now) and not (pref.vip_breaks_quiet and vip_attending(session, owner, names)):
        return skip("quiet_hours")
    text = compose(title, names, start, now, zone(owner_zone(session, owner)), gather(session, owner, title, names))
    if text is None:
        return skip("nothing_to_say")
    via = notify_owner(session, owner, text, {"kind": "meeting_prep", "event_id": event_id})
    audit(session, "proactive", "meeting_prep_sent", event_id=event_id, via=via)


@handler(SCAN_KIND)
def scan(session: Session, job: Job) -> None:
    """Look a couple of hours ahead, queue preps for what's coming, and book the next look."""
    owner = session.get(Person, uuid.UUID(job.payload["owner_id"]))
    if owner is None:
        return
    now = max(datetime.now(UTC), job.run_at)
    # The next look is booked first and in its own transaction: if the work below fails, this job's
    # transaction rolls back, and the chain must not end with it (issue #86).
    with session_scope() as booking:
        schedule_scan(booking, require(booking, Person, owner.id), now + timedelta(seconds=1))
    from superaide.proactive import calendar_watch

    try:
        calendar_watch.ensure(session, owner, now)  # push (when on) makes this scan run at once; never needed
    except Exception:
        log.warning("couldn't keep the calendar push channel", exc_info=True)
    events = calendar.events_between(session, owner, now, now + LOOKAHEAD)
    schedule_preps(session, owner, events, now)
