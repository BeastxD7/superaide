"""The daily briefing: what's on today, dates coming up, and what's waiting for the owner.

Built by code from what Ava already knows — no model call, so it costs nothing. Sent at
the owner's digest time (08:00 and/or 18:00 in their time zone); if there's nothing worth
saying, nothing is sent. Each send schedules the next one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.app.accounts import owner as hired_owner
from superaide.audit import audit
from superaide.context.audience import Audience, memory_filter
from superaide.db.models import Approval, Intention, Job, MemoryItem, Person
from superaide.intentions.service import owner_zone
from superaide.intentions.when import zone
from superaide.jobs.queue import enqueue, handler
from superaide.knowledge.store import preferences_for
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
        who = who.split()[-1] if who else ""
        label = (
            f"{who}'s {m.group('what').lower()}"
            if who and who.lower() not in {"my", "our"}
            else m.group("what").capitalize()
        )
        out.setdefault((when, label), Upcoming(when, label, who, str(note.id)))
    return sorted(out.values(), key=lambda u: u.when)


def compose(session: Session, owner: Person, now: datetime, slot: str) -> str | None:
    """The digest text, or None when there's nothing worth sending."""
    tz = owner_zone(session, owner)
    local = now.astimezone(zone(tz))
    day = local.date() if slot == "morning" else local.date() + timedelta(days=1)
    start = datetime.combine(day, time.min, zone(tz))
    end = start + timedelta(days=1)
    lines: list[str] = []

    due = session.scalars(
        select(Intention)
        .where(
            Intention.owner_person_id == owner.id,
            Intention.status == "open",
            Intention.due_at >= start,
            Intention.due_at < end,
        )
        .order_by(Intention.due_at)
    ).all()
    if due:
        heading = "**Today**" if slot == "morning" else "**Tomorrow**"
        lines.append(heading)
        lines += [f"- {i.due_at.astimezone(zone(tz)):%H:%M} {i.text}" for i in due]

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

    waiting = session.scalar(select(func.count()).select_from(Approval).where(Approval.status == "pending")) or 0
    if waiting:
        lines.append(f"**Waiting for your OK:** {waiting} on your dashboard")

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


@handler(JOB_KIND)
def send(session: Session, job: Job) -> None:
    import uuid

    owner = session.get(Person, uuid.UUID(job.payload["owner_id"]))
    if owner is None:
        return
    slot = str(job.payload.get("slot", "morning"))
    now = max(datetime.now(UTC), job.run_at)  # never schedule the next one before this one's own time
    if slot in slots_for(preferences_for(session, owner).digest):  # they may have changed their mind
        text = compose(session, owner, now, slot)
        if text:
            via = notify_owner(session, owner, text, {"kind": "digest", "slot": slot})
            audit(session, "proactive", "digest_sent", slot=slot, via=via)
        else:
            audit(session, "proactive", "digest_skipped_empty", slot=slot)
    schedule(session, owner, now + timedelta(minutes=1))  # the next one
