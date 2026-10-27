"""Spaced "is this still true?" (#49): ask the owner about important, lasting facts now and then.

Code picks what to ask and when; the model is not involved. A fact is due when it matters
(importance >= 0.7), doesn't fade (not time-bound), is owner-only and current, and its interval
since it was last confirmed (or learned) has passed: 30 days, then 90, 180, 365. At most
`memory_recheck_per_week` are put in front of the owner in any 7 days, and they only ever appear
as lines in the digest, never as an interruption. Off unless MEMORY_RECHECK_ENABLED.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.memory.consolidation import importance
from superaide.platform.audit import audit
from superaide.platform.config import get_settings
from superaide.platform.db.models import MemoryItem, Person

WEEK = timedelta(days=7)
ANSWERS = ("yes", "no", "changed")


def interval(verify_count: int) -> timedelta:
    steps = importance.RECHECK_INTERVAL_DAYS
    return timedelta(days=steps[min(verify_count, len(steps) - 1)])


def _owner_only(owner: Person):
    return (MemoryItem.allowed_kind == "persons") & (MemoryItem.allowed_person_ids == [owner.id])


def due(session: Session, owner: Person, now: datetime | None = None) -> list[MemoryItem]:
    """Facts that are due for a re-check and whose turn it is, within this week's allowance. Most
    important first, then the longest unconfirmed."""
    now = now or datetime.now(UTC)
    allowance = get_settings().memory_recheck_per_week
    asked = (
        session.scalar(
            select(func.count())
            .select_from(MemoryItem)
            .where(_owner_only(owner), MemoryItem.recheck_asked_at > now - WEEK, MemoryItem.valid_to.is_(None))
        )
        or 0
    )
    room = allowance - asked
    if room <= 0:
        return []
    rows = session.scalars(
        select(MemoryItem).where(
            _owner_only(owner),
            MemoryItem.valid_to.is_(None),
            MemoryItem.time_bound.is_(False),
            MemoryItem.importance >= importance.RECHECK_MIN_IMPORTANCE,
            (MemoryItem.recheck_asked_at.is_(None)) | (MemoryItem.recheck_asked_at <= now - WEEK),
        )
    ).all()
    ready = [m for m in rows if (m.last_verified_at or m.created_at) + interval(m.verify_count) <= now]
    ready.sort(key=lambda m: (-m.importance, m.last_verified_at or m.created_at))
    return ready[:room]


def digest_lines(session: Session, owner: Person, now: datetime) -> list[str]:
    """The digest section; marks what it shows as asked, so each fact is raised once per week."""
    if not get_settings().memory_recheck_enabled:
        return []
    items = due(session, owner, now)
    if not items:
        return []
    for m in items:
        m.recheck_asked_at = now
        audit(session, "memory", "recheck_asked", memory_id=m.id)
    return ["**Still true?** Answer Yes, No or Changed on your notebook page"] + [f"- {m.content}" for m in items]


def answer(session: Session, owner: Person, memory_id: uuid.UUID, verdict: str, new_content: str = "") -> MemoryItem:
    """Apply the owner's answer. yes: confirmed, the next check is further away. no: the fact is retired.
    changed: the old fact is retired and `new_content` is stored in its place. Raises LookupError when it
    isn't a current, owner-only fact of this owner, ValueError for a bad answer."""
    from superaide.memory.knowledge.store import remember_fact

    if verdict not in ANSWERS:
        raise ValueError("answer must be yes, no or changed")
    m = session.get(MemoryItem, memory_id)
    if m is None or m.valid_to is not None or owner.id not in (m.subject_person_ids or []):
        raise LookupError("not found")
    now = datetime.now(UTC)
    if verdict == "yes":
        m.last_verified_at, m.verify_count, m.recheck_asked_at = now, m.verify_count + 1, None
        m.confidence = 1.0
        audit(session, "memory", "recheck_confirmed", memory_id=m.id)
        return m
    m.valid_to = now
    audit(session, "memory", "recheck_retired", memory_id=m.id, verdict=verdict)
    if verdict == "changed":
        text = new_content.strip()
        if not text:
            raise ValueError("say what changed")
        new = remember_fact(session, owner=owner, content=text, category=m.category, source="owner_edit")
        if new is not None:
            new.last_verified_at, new.verify_count = now, 1
            audit(session, "memory", "superseded", memory_id=m.id, by=new.id)
            return new
    return m
