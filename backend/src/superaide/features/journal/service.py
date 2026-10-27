"""The journal's reads and writes, and the background job that finds predictions in the owner's chat."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from superaide.features.journal import extract, score
from superaide.jobs.queue import enqueue, handler
from superaide.memory.recall.lexicon import normalise
from superaide.platform.audit import audit
from superaide.platform.db.models import Event, Job, JournalSetting, Person, Prediction
from superaide.platform.db.session import rows_affected

log = logging.getLogger(__name__)

JOB_KIND = "journal_from_turn"
TOPICS = ("timeline", "people", "money", "work", "other")


def is_on(session: Session, owner_id: uuid.UUID) -> bool:
    row = session.scalar(select(JournalSetting).where(JournalSetting.owner_person_id == owner_id))
    return bool(row and row.enabled)


def set_enabled(session: Session, owner_id: uuid.UUID, enabled: bool) -> int:
    """Turn the journal on or off. Turning it off deletes every prediction: the number returned."""
    row = session.scalar(select(JournalSetting).where(JournalSetting.owner_person_id == owner_id))
    if row is None:
        row = JournalSetting(owner_person_id=owner_id)
        session.add(row)
    deleted = 0
    row.enabled = enabled
    row.enabled_at = datetime.now(UTC) if enabled else None
    if not enabled:
        deleted = rows_affected(session.execute(delete(Prediction).where(Prediction.owner_person_id == owner_id)))
    session.flush()
    audit(session, "journal", "enabled" if enabled else "disabled", person_id=owner_id, deleted=deleted)
    return deleted


def add(
    session: Session,
    owner_id: uuid.UUID,
    *,
    statement: str,
    confidence_pct: int,
    due_on: date | None = None,
    reason: str = "",
    topic: str = "other",
    quote: str = "",
    source: str = "manual",
    inferred: bool = False,
    event_id: uuid.UUID | None = None,
) -> Prediction | None:
    """Save one prediction. None if the owner already has the same one open."""
    statement = " ".join(statement.split())[:500]
    open_same = session.scalars(
        select(Prediction.statement).where(Prediction.owner_person_id == owner_id, Prediction.status == "open")
    ).all()
    if any(normalise(s) == normalise(statement) for s in open_same):
        return None
    p = Prediction(
        owner_person_id=owner_id,
        statement=statement,
        quote=quote[:1000],
        reason=" ".join(reason.split())[:500],
        topic=topic if topic in TOPICS else "other",
        confidence_pct=max(50, min(99, confidence_pct)),
        confidence_inferred=inferred,
        due_on=due_on,
        source=source,
        source_event_id=event_id,
    )
    session.add(p)
    session.flush()
    audit(session, "journal", "prediction_added", prediction_id=p.id, source=source)
    return p


def view(p: Prediction, today: date) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "statement": p.statement,
        "quote": p.quote,
        "reason": p.reason,
        "topic": p.topic,
        "confidence_pct": p.confidence_pct,
        "inferred": p.confidence_inferred,
        "due_on": p.due_on.isoformat() if p.due_on else None,
        "due": p.status == "open" and p.due_on is not None and p.due_on <= today,
        "source": p.source,
        "status": p.status,
        "came_true": p.came_true,
        "note": p.resolution_note,
        "at": p.created_at.isoformat(),
        "resolved_at": p.resolved_at.isoformat() if p.resolved_at else None,
    }


def resolved_rows(session: Session, owner_id: uuid.UUID) -> list[score.Resolved]:
    rows = session.scalars(
        select(Prediction).where(
            Prediction.owner_person_id == owner_id, Prediction.status == "resolved", Prediction.came_true.is_not(None)
        )
    ).all()
    return [score.Resolved(r.topic, r.confidence_pct, bool(r.came_true)) for r in rows]


def page(session: Session, owner_id: uuid.UUID, today: date) -> dict[str, Any]:
    on = is_on(session, owner_id)
    preds = session.scalars(
        select(Prediction).where(Prediction.owner_person_id == owner_id).order_by(Prediction.created_at.desc())
    ).all()
    resolved = resolved_rows(session, owner_id)
    return {
        "enabled": on,
        "predictions": [view(p, today) for p in preds],
        "calibration": score.calibration(resolved) if on else None,
        "resolved": len(resolved),
        "needed": score.MIN_RESOLVED,
    }


def resolve(session: Session, p: Prediction, *, came_true: bool, note: str = "") -> None:
    p.status = "resolved"
    p.came_true = came_true
    p.resolution_note = " ".join(note.split())[:500]
    p.resolved_at = datetime.now(UTC)
    audit(session, "journal", "prediction_resolved", prediction_id=p.id, came_true=came_true)


# --- finding predictions in chat ---------------------------------------------------------------


def schedule(session: Session, event: Event) -> None:
    """Queue reading one inbound owner message, once, if (and only if) the owner turned the journal on."""
    if event.resolved_person_id is None or len(event.content.strip()) < 20:
        return
    if is_on(session, event.resolved_person_id):
        enqueue(session, JOB_KIND, {"event_id": str(event.id)}, dedupe_key=f"journal:{event.id}", max_attempts=3)


@handler(JOB_KIND)
def read_turn(session: Session, job: Job) -> None:
    from superaide.brain.wiring import brain_ready, build_llm
    from superaide.intentions.service import owner_zone
    from superaide.intentions.when import zone
    from superaide.vault.store import runtime_settings

    event = session.get(Event, uuid.UUID(job.payload["event_id"]))
    if event is None or event.resolved_person_id is None or event.direction != "in":
        return
    owner = session.get(Person, event.resolved_person_id)
    if owner is None or not is_on(session, owner.id):
        return  # switched off since: nothing is read, nothing is kept
    settings = runtime_settings()
    if not brain_ready(settings):
        return
    agent = SimpleNamespace(settings=settings, llm=build_llm(settings))
    local = event.created_at.astimezone(zone(owner_zone(session, owner)))
    found = extract.extract(agent, owner_name=owner.display_name, message=event.content, today=f"{local:%A %d %B %Y}")
    if found is None:
        raise RuntimeError("couldn't read the message just now")  # the job retries later
    for c in found:
        add(
            session,
            owner.id,
            statement=c.statement,
            confidence_pct=c.confidence_pct,
            due_on=extract.parse_due(c.due_on),
            reason=c.reason,
            topic=c.topic,
            quote=c.quote,
            source="chat",
            inferred=c.inferred,
            event_id=event.id,
        )
