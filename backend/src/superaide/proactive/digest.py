"""The daily briefing: what's on today, dates coming up, and what's waiting for the owner.

Built by code from what Ava already knows — no model call, so it costs nothing. Sent at
the owner's digest time (08:00 and/or 18:00 in their time zone); if there's nothing worth
saying, nothing is sent. Each send schedules the next one.

With the Google Calendar skill on and connected, the day's events lead the digest and each
one gets a pre-meeting prep job (see `prep`). Without it, or if Google is unreachable, the
calendar part is left out quietly.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.app.accounts import owner as hired_owner
from superaide.integrations.calendar import skill as calendar
from superaide.integrations.google.client import event_text
from superaide.intentions.service import owner_zone
from superaide.intentions.when import zone
from superaide.jobs.queue import enqueue, handler
from superaide.memory.audience import Audience, memory_filter
from superaide.memory.knowledge.store import preferences_for
from superaide.platform.audit import audit
from superaide.platform.db.models import Approval, Intention, Job, MemoryItem, Person
from superaide.platform.db.session import require, session_scope
from superaide.policy.binding import waiting as waiting_clause
from superaide.proactive import prep
from superaide.proactive.notify import notify_owner

JOB_KIND = "send_digest"
SLOTS = {"morning": time(8, 0), "evening": time(18, 0)}
DATES_AHEAD = timedelta(days=7)
MONTHS = "january february march april may june july august september october november december".split()

# "Priya's birthday is on 14 November", "our anniversary: 3 Dec", "Mum's birthday is November 14"
DATE_FACT = re.compile(
    r"(?P<who>[\w' -]{0,40}?)\s*(?P<what>birthday|anniversary)\b[^.\n]*?"
    r"(?:(?P<d1>\d{1,2})(?:st|nd|rd|th)?\s+(?P<m1>[a-z]{3,9})|(?P<m2>[a-z]{3,9})\s+(?P<d2>\d{1,2})(?:st|nd|rd|th)?)",
    re.I,
)


@dataclass
class Upcoming:
    when: date
    label: str  # "Priya's birthday"
    who: str  # "Priya" (for related notes), or ""
    note_id: str


def slots_for(digest: str) -> list[str]:
    return {"morning": ["morning"], "evening": ["evening"], "both": ["morning", "evening"]}.get(digest, [])


def _month(name: str) -> int | None:
    name = name.lower()
    for i, m in enumerate(MONTHS, start=1):
        if m.startswith(name[:3]) and (len(name) == 3 or m.startswith(name)):
            return i
    return None


def _first_name(phrase: str) -> str:
    """The person a date belongs to: "Priya Tan" → "Priya", "my sister Anya" → "Anya", "mum" → "mum"."""
    names = re.findall(r"\b[A-Z][\w-]*(?:\s+[A-Z][\w-]*)*", phrase)
    if names:
        return names[-1].split()[0]
    return phrase.split()[-1] if phrase.split() else ""


def upcoming_dates(notes: list[MemoryItem], today: date) -> list[Upcoming]:
    out: dict[tuple[date, str], Upcoming] = {}
    for note in notes:
        m = DATE_FACT.search(note.content)
        if not m:
            continue
        month = _month(m.group("m1") or m.group("m2") or "")
        day = int(m.group("d1") or m.group("d2") or 0)
        if month is None or not 1 <= day <= 31:
            continue
        try:
            when = date(today.year, month, day)
            if when < today:
                when = date(today.year + 1, month, day)
        except ValueError:
            continue
        if when - today > DATES_AHEAD:
            continue
        who = re.sub(r"(?:'s|’s)$", "", (m.group("who") or "").strip()).strip()
        who = _first_name(who)
        label = (
            f"{who}'s {m.group('what').lower()}"
            if who and who.lower() not in {"my", "our"}
            else m.group("what").capitalize()
        )
        out.setdefault((when, label), Upcoming(when, label, who, str(note.id)))
    return sorted(out.values(), key=lambda u: u.when)


def day_window(session: Session, owner: Person, now: datetime, slot: str) -> tuple[datetime, datetime]:
    """The day a digest covers: today for the morning one, tomorrow for the evening one."""
    tz = zone(owner_zone(session, owner))
    local = now.astimezone(tz)
    day = local.date() if slot == "morning" else local.date() + timedelta(days=1)
    start = datetime.combine(day, time.min, tz)
    return start, start + timedelta(days=1)


def event_line(event: dict[str, Any], tz: Any) -> str:
    title = event_text(event, tz)[0]
    start, end = event.get("start") or {}, event.get("end") or {}
    if "dateTime" not in start:
        return f"- All day: {title}"
    try:
        s = datetime.fromisoformat(start["dateTime"]).astimezone(tz)
        e = datetime.fromisoformat(end.get("dateTime", start["dateTime"])).astimezone(tz)
    except ValueError:
        return f"- {title}"
    names = prep.attendee_names(event)
    return f"- {s:%H:%M}–{e:%H:%M} {title}" + (f" (with {', '.join(names[:4])})" if names else "")


def compose(
    session: Session, owner: Person, now: datetime, slot: str, events: list[dict[str, Any]] | None = None
) -> str | None:
    """The digest text, or None when there's nothing worth sending. `events` are the day's
    calendar events; when not given they're read from the calendar (if connected)."""
    tz = owner_zone(session, owner)
    local = now.astimezone(zone(tz))
    start, end = day_window(session, owner, now, slot)
    lines: list[str] = []

    if events is None:
        events = calendar.events_between(session, owner, start, end)
    shown = [e for e in events if e.get("status") != "cancelled"]
    if shown:
        lines.append("**Calendar**" if slot == "morning" else "**Calendar tomorrow**")
        lines += [event_line(e, zone(tz)) for e in shown]

    due = session.scalars(
        select(Intention)
        .where(
            Intention.owner_person_id == owner.id,
            Intention.status == "open",
            Intention.delivered_at.is_(None),  # one that already went off is not "still to do"
            Intention.due_at >= start,
            Intention.due_at < end,
        )
        .order_by(Intention.due_at)
    ).all()
    if due:
        heading = "**Today**" if slot == "morning" else "**Tomorrow**"
        lines.append(heading)
        lines += [f"- {i.due_at.astimezone(zone(tz)):%H:%M} {i.text}" for i in due]

    from superaide.platform.config import get_settings

    if slot == "morning" and local.weekday() == 0 and get_settings().second_opinion_enabled:  # Mondays
        from superaide.trust.second_opinion import agreement_line

        if line := agreement_line(session, owner):
            lines += ["**My agreement rate**", line]

    audience = Audience(person_ids={owner.id}, min_trust=2)
    notes = session.scalars(select(MemoryItem).where(memory_filter(audience))).all()
    soon = upcoming_dates(list(notes), local.date())
    if soon:
        lines.append("**Coming up**")
        for u in soon:
            days = (u.when - local.date()).days
            when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
            extra = ""
            if u.who:
                related = [
                    n.content
                    for n in notes
                    if u.who.lower() in n.content.lower() and str(n.id) != u.note_id and len(n.content) < 160
                ][:2]
                if related:
                    extra = " — " + "; ".join(r.rstrip(".") for r in related)
            lines.append(f"- {u.label} {when} ({u.when:%a %d %b}){extra}")

    waiting = session.scalar(select(func.count()).select_from(Approval).where(waiting_clause())) or 0
    if waiting:
        lines.append(f"**Waiting for your OK:** {waiting} on your dashboard")

    from superaide.triage.digest import digest_lines  # ADR 0010: [] while TRIAGE_ENABLED is off

    lines += digest_lines(session, owner, now)
    from superaide.platform.config import get_settings

    if not get_settings().triage_enabled:  # with triage on, messages taken are in its sections
        from superaide.reception.service import digest_lines as reception_lines
        from superaide.triage.digest import last_digest_at

        lines += reception_lines(session, owner, last_digest_at(session, owner, now), now)
    from superaide.asklink.service import digest_lines as ask_lines  # [] while ASK_LINK_ENABLED is off

    lines += ask_lines(session, owner, now)
    from superaide.memory.consolidation.recheck import (
        digest_lines as recheck_lines,  # [] while MEMORY_RECHECK_ENABLED is off
    )

    lines += recheck_lines(session, owner, now)
    if not lines:
        return None
    first = owner.display_name.split()[0]
    hello = f"Good morning, {first}." if slot == "morning" else f"Good evening, {first}. Here's tomorrow."
    return hello + "\n\n" + "\n".join(lines)


def next_run(digest: str, tz: str, now: datetime) -> tuple[datetime, str] | None:
    """The next digest moment after `now` for this preference, and which slot it is."""
    local = now.astimezone(zone(tz))
    candidates = []
    for slot in slots_for(digest):
        at = datetime.combine(local.date(), SLOTS[slot], zone(tz))
        if at <= local:
            at += timedelta(days=1)
        candidates.append((at, slot))
    return min(candidates) if candidates else None


def schedule(session: Session, owner: Person, now: datetime | None = None) -> Job | None:
    pref = preferences_for(session, owner)
    found = next_run(pref.digest, pref.timezone or "UTC", now or datetime.now(UTC))
    if found is None:
        return None
    at, slot = found
    key = f"digest:{owner.id}:{at.date().isoformat()}:{slot}"
    return enqueue(
        session, JOB_KIND, {"owner_id": str(owner.id), "slot": slot}, run_at=at.astimezone(UTC), dedupe_key=key
    )


def ensure_scheduled(session: Session) -> None:
    """Make sure the owner's next digest is queued (safe to call any time)."""
    owner = hired_owner(session)
    if owner is not None:
        schedule(session, owner)
        prep.schedule_scan(session, owner)


@handler(JOB_KIND)
def send(session: Session, job: Job) -> None:
    owner = session.get(Person, uuid.UUID(job.payload["owner_id"]))
    if owner is None:
        return
    slot = str(job.payload.get("slot", "morning"))
    now = max(datetime.now(UTC), job.run_at)  # never schedule the next one before this one's own time
    # The next one is booked first and in its own transaction: if the work below fails, this job's
    # transaction rolls back, and the chain must not end with it (issue #86).
    with session_scope() as booking:
        schedule(booking, require(booking, Person, owner.id), now + timedelta(minutes=1))
    if slot in slots_for(preferences_for(session, owner).digest):  # they may have changed their mind
        start, end = day_window(session, owner, now, slot)
        events = calendar.events_between(session, owner, start, end)
        prep.schedule_preps(session, owner, events, now)
        text = compose(session, owner, now, slot, events)
        if text:
            via = notify_owner(session, owner, text, {"kind": "digest", "slot": slot})
            audit(session, "proactive", "digest_sent", slot=slot, via=via)
        else:
            audit(session, "proactive", "digest_skipped_empty", slot=slot)
