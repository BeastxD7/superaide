"""Pattern mining is code over the owner's own rows: counts, deltas and repeats. Nothing here calls a model.

Each finding is a sentence from a fixed template plus its receipts: the exact rows (a reminder, a count over
a date range) the sentence was made from, with a link to where the owner can open them. A finding without
receipts is never produced. Wellbeing rule: nothing about mood, health or feelings, and nothing about other
people except what the owner themself wrote in their own reminders.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.memory.recall.lexicon import normalise
from superaide.platform.db.models import Event, Intention

LOOKBACK_WEEKS = 8
REPEAT_MIN = 3  # the same reminder set this many times, in this many different weeks
DROPPED_MIN = 2
DROPPED_GRACE = timedelta(days=3)
SHIFT_MIN_BEFORE = 5  # fewer messages than this the week before is too little to compare
SHIFT_RATIO = 0.5  # a change of half or more
MAX_FINDINGS = 3
KINDS = ("repeated_reminders", "dropped_commitments", "activity_shift")


@dataclass
class Finding:
    kind: str
    pattern_key: str  # names the thing (not the week), so "don't mention again" survives to next week
    text: str
    href: str
    receipts: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "pattern_key": self.pattern_key,
            "text": self.text,
            "href": self.href,
            "receipts": self.receipts,
            "feedback": None,
        }


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _receipt(i: Intention) -> dict[str, Any]:
    return {
        "type": "reminder",
        "id": str(i.id),
        "label": i.text,
        "at": i.created_at.isoformat(),
        "due": i.due_at.isoformat(),
        "status": i.status,
    }


def repeated_reminders(session: Session, owner_id: uuid.UUID, now: datetime) -> list[Finding]:
    since = now - timedelta(weeks=LOOKBACK_WEEKS)
    rows = session.scalars(
        select(Intention)
        .where(Intention.owner_person_id == owner_id, Intention.created_at >= since)
        .order_by(Intention.created_at)
    ).all()
    groups: dict[str, list[Intention]] = defaultdict(list)
    for r in rows:
        key = normalise(r.text)
        if key:
            groups[key].append(r)
    out = []
    for key, items in groups.items():
        weeks = {week_start(i.created_at.date()) for i in items}
        if len(items) >= REPEAT_MIN and len(weeks) >= REPEAT_MIN:
            out.append(
                Finding(
                    "repeated_reminders",
                    f"repeat:{key[:80]}",
                    f"You set a reminder for “{items[-1].text}” {len(items)} times across {len(weeks)} weeks.",
                    "/dashboard/reminders",
                    [_receipt(i) for i in items],
                )
            )
    return sorted(out, key=lambda f: -len(f.receipts))


def dropped_commitments(session: Session, owner_id: uuid.UUID, now: datetime) -> list[Finding]:
    since = now - timedelta(weeks=LOOKBACK_WEEKS)
    rows = session.scalars(
        select(Intention)
        .where(
            Intention.owner_person_id == owner_id,
            Intention.status == "open",
            Intention.delivered_at.is_not(None),
            Intention.due_at < now - DROPPED_GRACE,
            Intention.due_at >= since,
        )
        .order_by(Intention.due_at)
    ).all()
    if len(rows) < DROPPED_MIN:
        return []
    return [
        Finding(
            "dropped_commitments",
            "dropped",
            f"{len(rows)} reminders went off more than three days ago and are still open.",
            "/dashboard/reminders",
            [_receipt(i) for i in rows],
        )
    ]


def activity_shift(session: Session, owner_id: uuid.UUID, now: datetime) -> list[Finding]:
    this_start, last_start = now - timedelta(days=7), now - timedelta(days=14)

    def count(a: datetime, b: datetime) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(
                    Event.resolved_person_id == owner_id,
                    Event.direction == "in",
                    Event.created_at >= a,
                    Event.created_at < b,
                )
            )
            or 0
        )

    now_n, before_n = count(this_start, now), count(last_start, this_start)
    if before_n < SHIFT_MIN_BEFORE or abs(now_n - before_n) < SHIFT_RATIO * before_n:
        return []
    word = "more" if now_n > before_n else "fewer"
    fmt = "%d %b"
    return [
        Finding(
            "activity_shift",
            "activity",
            f"You sent {now_n} messages in the last 7 days, {word} than the {before_n} the week before.",
            "/dashboard/chat",
            [
                {"type": "count", "label": f"{this_start:{fmt}} to {now:{fmt}}", "count": now_n},
                {"type": "count", "label": f"{last_start:{fmt}} to {this_start:{fmt}}", "count": before_n},
            ],
        )
    ]


MINERS = (repeated_reminders, dropped_commitments, activity_shift)


def mine(session: Session, owner_id: uuid.UUID, now: datetime | None = None) -> list[Finding]:
    now = now or datetime.now(UTC)
    found: list[Finding] = []
    for miner in MINERS:
        found.extend(miner(session, owner_id, now))
    return [f for f in found if f.receipts]
