"""Set, clear and read the owner's presence, and the two things code applies it to: what a visitor is told, and who
gets through. One current state per owner: setting a new one ends the old one."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.memory.models import Contact
from superaide.platform.audit import audit
from superaide.presence.models import PresenceState
from superaide.presence.parse import Parsed

NOTICE = "They're not available until {at}. Your message is passed on, and they'll see it after that."


def _local(at: datetime, tz: str, now: datetime | None = None) -> str:
    """`now` is the caller's clock (the same one that decided `at`), so "today" never disagrees with it."""
    local = at.astimezone(ZoneInfo(tz))
    return (
        f"{local:%H:%M} {local.tzname() or tz}"
        if local.date() == (now or datetime.now(UTC)).astimezone(ZoneInfo(tz)).date()
        else (f"{local:%a %H:%M} {local.tzname() or tz}")
    )


def visitor_line(until: datetime, tz: str, now: datetime | None = None) -> str:
    """The only words a visitor hears: no kind, no reason, no place. A fixed template filled with the end time."""
    return NOTICE.format(at=_local(until, tz, now))


def active(session: Session, person_id: uuid.UUID, now: datetime | None = None) -> PresenceState | None:
    """The state in force now, or None. A state past its end is marked ended and never applies."""
    now = now or datetime.now(UTC)
    rows = session.scalars(
        select(PresenceState)
        .where(PresenceState.person_id == person_id, PresenceState.status == "active")
        .order_by(PresenceState.created_at.desc())
    ).all()
    current = None
    for r in rows:
        if r.valid_until <= now:
            r.status = "ended"
        elif r.valid_from <= now and current is None:
            current = r
    return current


def set_state(
    session: Session, person_id: uuid.UUID, parsed: Parsed, tz: str, now: datetime | None = None
) -> PresenceState:
    now = now or datetime.now(UTC)
    for old in session.scalars(
        select(PresenceState).where(PresenceState.person_id == person_id, PresenceState.status == "active")
    ):
        old.status = "cleared"
    row = PresenceState(
        person_id=person_id, kind=parsed.kind, valid_from=now, valid_until=parsed.until,
        allow_relations=list(parsed.allow_relations), visitor_line=visitor_line(parsed.until, tz, now),
    )  # fmt: skip
    session.add(row)
    session.flush()
    audit(session, "presence", "set", kind=row.kind, until=row.valid_until.isoformat(), allow=row.allow_relations)
    return row


def clear(session: Session, person_id: uuid.UUID, now: datetime | None = None) -> bool:
    row = active(session, person_id, now)
    if row is None:
        return False
    row.status = "cleared"
    audit(session, "presence", "cleared", kind=row.kind)
    return True


def lets_through(session: Session, state: PresenceState, owner_id: uuid.UUID, sender_id: uuid.UUID | None) -> bool:
    """Does this sender get past the state? Only a known contact whose relationship (or VIP flag) the owner named.
    Unknown senders never do; the relationship comes from the database, never from what the sender typed."""
    if sender_id is None or not state.allow_relations:
        return False
    c = session.scalar(
        select(Contact).where(
            Contact.owner_person_id == owner_id, Contact.person_id == sender_id, Contact.removed_at.is_(None)
        )
    )
    if c is None:
        return False
    return c.relationship in state.allow_relations or ("vip" in state.allow_relations and c.vip)


def status_text(state: PresenceState | None, tz: str) -> str:
    if state is None:
        return "No presence status is set: you are reachable as usual."
    who = f" ({', '.join(state.allow_relations)} still get through)" if state.allow_relations else ""
    return f"You are {state.kind} until {_local(state.valid_until, tz)}{who}."


def describe(state: PresenceState, tz: str) -> dict[str, Any]:
    return {"kind": state.kind, "until": _local(state.valid_until, tz), "allow": list(state.allow_relations)}
