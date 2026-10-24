"""Short facts from the owner's calendar and verified mail (owner decision, 2026-10).

Behind CONNECTOR_FACTS_ENABLED (off by default). After a calendar read or a Gmail triage, a background job
asks the small model for at most FACTS_PER_ITEM short facts ("the dentist moved to Friday 3pm") and keeps
them as owner-only notes. Rules, all in code:
- Never the raw text. A job carries only the item's id; the text is read again from where it already lives
  (the calendar, or the logged email's own cleaned excerpt), shown to the model, and dropped.
- Provenance is the item id (`memory_item.source_ref` = "calendar:<id>" or "gmail:<id>").
- Audience unchanged: owner-only, like every learned fact. Mail is another person's claim: trust rank 4, and
  hearsay under the Echo Filter with the sender as its origin. Calendar is the owner's own record (rank 3).
  Neither may state the owner's rules (urgency, sharing, availability, preferences): those wait in review.
- Only mail from a sender Gmail vouched for (DMARC), never newsletters or lists, never payment, legal or
  credential mail. Calendar events are the owner's own.
- At most FACTS_PER_ITEM per item and CONNECTOR_FACTS_PER_DAY a day; the model call reserves its cost against
  the spend cap like any other, and a refused call just means nothing is kept.
- Disconnecting Google or removing the Gmail or Calendar capability deletes the facts (`erase_source`), as does
  `POST /app/notebook/sources/{gmail|calendar}/erase`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.db.models import Event, Job, MemoryItem, Person
from superaide.jobs.queue import enqueue, handler
from superaide.knowledge.store import remember_fact

log = logging.getLogger(__name__)

JOB_KIND = "connector_facts"
FACTS_PER_ITEM = 3
SOURCES = ("gmail", "calendar")
MAX_PER_CALL = 10  # calendar events looked at per read


def enabled() -> bool:
    from superaide.config import get_settings

    return bool(get_settings().connector_facts_enabled)


def facts_today(session: Session) -> int:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        session.scalar(
            select(func.count())
            .select_from(MemoryItem)
            .where(MemoryItem.source.in_(SOURCES), MemoryItem.created_at >= start)
        )
        or 0
    )


def room_today(session: Session) -> int:
    from superaide.config import get_settings

    return max(0, get_settings().connector_facts_per_day - facts_today(session))


# --- scheduling ---------------------------------------------------------------------------------------


def schedule_calendar(session: Session, events: list[dict[str, Any]]) -> None:
    """After the calendar was read: one job per event (new, or changed since last time)."""
    if not enabled():
        return
    for e in events[:MAX_PER_CALL]:
        eid = str(e.get("id") or "")
        if eid:
            version = str(e.get("updated") or "")
            enqueue(session, JOB_KIND, {"kind": "calendar", "id": eid}, dedupe_key=f"cf:cal:{eid}:{version}"[:200],
                    max_attempts=2)  # fmt: skip


def schedule_gmail(session: Session, event: Event) -> None:
    """After a mail was triaged: one job for it (the email's own logged excerpt is what gets read)."""
    if enabled():
        enqueue(session, JOB_KIND, {"kind": "gmail", "id": str(event.id)}, dedupe_key=f"cf:gmail:{event.id}",
                max_attempts=2)  # fmt: skip


# --- the job --------------------------------------------------------------------------------------------


def _mail_worth_reading(event: Event) -> bool:
    from superaide.skills.gmail_inbox import NEVER_QUOTED

    meta = event.meta or {}
    subject = str(meta.get("subject") or "")
    return bool(meta.get("verified")) and not meta.get("bulk") and not NEVER_QUOTED.search(f"{subject} {event.content}")


def _keep(session: Session, owner: Person, *, source: str, ref: str, facts: list[Any], author: str | None) -> int:
    """Store at most FACTS_PER_ITEM facts, within today's room. Earlier facts from the same item are retired
    (the event moved, the mail was followed up) and stay queryable by valid time."""
    now = datetime.now(UTC)
    session.execute(
        update(MemoryItem)
        .where(MemoryItem.source_ref == ref, MemoryItem.valid_to.is_(None), MemoryItem.locked.is_(False))
        .values(valid_to=now)
    )
    kept = 0
    for f in facts[:FACTS_PER_ITEM]:
        if kept >= room_today(session):
            break
        item = remember_fact(
            session, owner=owner, content=f.content[:200],
            category=f.category,  # a rule category from mail or the calendar waits in review (trust.needs_review)
            source=source, confidence=f.confidence, importance=f.importance, time_bound=f.time_bound,
            origin_author=author, source_ref=ref,
        )  # fmt: skip
        if item is not None:
            kept += 1
    return kept


@handler(JOB_KIND)
def run(session: Session, job: Job) -> None:
    from superaide.app.accounts import owner as instance_owner
    from superaide.brain.wiring import brain_ready, build_llm
    from superaide.knowledge.facts import extract_facts
    from superaide.vault.store import runtime_settings

    if not enabled():
        return
    owner = instance_owner(session)
    settings = runtime_settings()
    if owner is None or not brain_ready(settings) or room_today(session) <= 0:
        return
    kind, item_id = str(job.payload.get("kind")), str(job.payload.get("id"))
    author: str | None = None
    if kind == "gmail":
        event = session.get(Event, uuid.UUID(item_id))
        if event is None or not _mail_worth_reading(event):
            return
        meta = event.meta or {}
        text, label, ref = event.content, "an email the owner received", f"gmail:{meta.get('gmail_id') or item_id}"
        author = str(meta.get("from_domain") or meta.get("from_name") or "") or None
    elif kind == "calendar":
        from superaide.connectors import google
        from superaide.intentions.service import owner_zone
        from superaide.intentions.when import zone
        from superaide.skills import calendar

        try:
            event_json = calendar.fresh_event(session, owner, item_id)
        except calendar.Unknown:
            return
        if event_json is None:
            erase_ref(session, f"calendar:{item_id}")  # the event was deleted: so are its facts
            return
        text = google.event_text(event_json, zone(owner_zone(session, owner)))[1]
        label, ref = "the owner's calendar", f"calendar:{item_id}"
    else:
        return
    local = datetime.now(UTC)
    agent = SimpleNamespace(settings=settings, llm=build_llm(settings))
    facts = extract_facts(agent, owner_name=owner.display_name, text=text, source=label, today=f"{local:%A %d %B %Y}")
    if facts is None:
        raise RuntimeError("couldn't read the item just now")  # one retry, then it is dropped
    n = _keep(session, owner, source=kind, ref=ref, facts=facts, author=author)
    audit(session, "memory", "connector_facts", source=kind, ref=ref, facts=n)


# --- deleting ---------------------------------------------------------------------------------------------


def erase_ref(session: Session, ref: str) -> int:
    return _delete(session, MemoryItem.source_ref == ref)


def erase_source(session: Session, source: str) -> int:
    """Delete every fact read from this connector (calendar or gmail), live or retired, with its vectors."""
    return _delete(session, or_(MemoryItem.source == source, MemoryItem.source_ref.like(f"{source}:%")))


def _delete(session: Session, where: Any) -> int:
    ids = list(session.scalars(select(MemoryItem.id).where(where)))
    if not ids:
        return 0
    session.execute(update(MemoryItem).where(MemoryItem.superseded_by.in_(ids)).values(superseded_by=None))
    session.execute(update(MemoryItem).where(MemoryItem.conflicts_with_id.in_(ids)).values(conflicts_with_id=None))
    session.execute(delete(MemoryItem).where(MemoryItem.id.in_(ids)))  # vectors and origins cascade
    audit(session, "memory", "connector_facts_erased", facts=len(ids))
    return len(ids)
