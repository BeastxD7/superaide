"""The change log of the nightly sleep job and its undo (#415).

Every note the job retires (an exact duplicate, a near-duplicate the small model called "same", or a note a newer
one replaces) writes one `consolidation_change` row: what, why, who decided, where the note came from, and the
fields before and after, for the retired note and for the note that stays (which absorbs sources and confidence).
The table is append-only. Undo is a compensating write: it puts the "before" fields back and appends an `undo`
row that points at the change it reverses (the audit trail), so nothing is ever updated in place or deleted, and
a change can be undone once. No model is involved: the owner clicks, code puts the fields back.

An undo is refused when the note is no longer in the state the job left it (the owner forgot it, or something else
retired it): the owner's later choice wins. The note that stays is put back only if it is still exactly as the job
left it; otherwise it keeps its newer details and only the retired note comes back.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from superaide.memory.models import ConsolidationChange, ConsolidationRun, MemoryItem
from superaide.platform.audit import audit

# What the job does today. "rewrite" and "decay" are not steps of the job (decay is computed when a note is read,
# nothing is rewritten), so they have no record; a new step adds its kind here and a way to reverse it in `undo`.
KINDS = ("duplicate", "near_duplicate", "contradiction")
UNDO = "undo"

WHY = {
    "duplicate": (
        "code",
        "Same words as another note once capitals and punctuation are ignored; the better-sourced one stays.",
    ),
    "near_duplicate": (
        "model",
        "The small model judged these two notes to say the same thing; the better-sourced one stays.",
    ),
    "contradiction": (
        "model",
        "The small model judged that the newer note replaces this one; this one points at its successor.",
    ),
}


class ChangeError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _iso(value: datetime | None) -> str | None:
    # Always UTC, so the same instant reads the same whatever time zone the database session reports.
    return value.astimezone(UTC).isoformat() if value is not None else None


def retired_state(m: MemoryItem) -> dict[str, Any]:
    return {"valid_to": _iso(m.valid_to), "superseded_by": str(m.superseded_by) if m.superseded_by else None}


def keeper_state(m: MemoryItem) -> dict[str, Any]:
    return {"confidence": m.confidence, "source_event_ids": [str(i) for i in (m.source_event_ids or [])]}


def snapshot(retired: MemoryItem, keeper: MemoryItem) -> dict[str, Any]:
    return {"retired": retired_state(retired), "keeper": keeper_state(keeper)}


def record(
    session: Session, run: ConsolidationRun, kind: str, retired: MemoryItem, keeper: MemoryItem,
    before: dict[str, Any], after: dict[str, Any],
) -> ConsolidationChange:  # fmt: skip
    decided_by, reason = WHY[kind]
    change = ConsolidationChange(
        run_id=run.id, owner_person_id=run.owner_person_id, kind=kind, memory_id=retired.id, kept_id=keeper.id,
        reason=reason, decided_by=decided_by, source=retired.source or "", before=before, after=after,
    )  # fmt: skip
    session.add(change)
    return change


def undone_ids(session: Session, change_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not change_ids:
        return set()
    return {
        i
        for i in session.scalars(
            select(ConsolidationChange.undoes_id).where(ConsolidationChange.undoes_id.in_(change_ids))
        )
        if i is not None
    }


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def undo(session: Session, owner_id: uuid.UUID, change_id: uuid.UUID) -> ConsolidationChange:
    """Reverse one change. Raises ChangeError: 404 not the owner's, 409 already undone or changed since."""
    ch = session.get(ConsolidationChange, change_id)
    if ch is None or ch.owner_person_id != owner_id or ch.kind not in KINDS:
        raise ChangeError(404, "Not found")
    m = session.get(MemoryItem, ch.memory_id)
    if m is None or owner_id not in (m.subject_person_ids or []):
        raise ChangeError(404, "Not found")
    if session.scalar(select(ConsolidationChange.id).where(ConsolidationChange.undoes_id == ch.id)) is not None:
        raise ChangeError(409, "Already undone.")
    if retired_state(m) != ch.after["retired"]:
        raise ChangeError(409, "This note has changed since the night it was tidied, so it was left as it is.")
    keeper = session.get(MemoryItem, ch.kept_id) if ch.kept_id else None
    restore_keeper = keeper is not None and keeper_state(keeper) == ch.after["keeper"]
    here = {"retired": retired_state(m), "keeper": keeper_state(keeper) if keeper else ch.after["keeper"]}
    m.valid_to = _when(ch.before["retired"]["valid_to"])
    m.superseded_by = uuid.UUID(s) if (s := ch.before["retired"]["superseded_by"]) else None
    if keeper is not None and restore_keeper:
        keeper.confidence = ch.before["keeper"]["confidence"]
        keeper.source_event_ids = [uuid.UUID(i) for i in ch.before["keeper"]["source_event_ids"]]
    now = {"retired": retired_state(m), "keeper": keeper_state(keeper) if keeper else ch.after["keeper"]}
    reason = "Undone by the owner."
    if keeper is not None and not restore_keeper:
        reason += " The kept note had newer details and was left."
    row = ConsolidationChange(
        run_id=ch.run_id, owner_person_id=owner_id, kind=UNDO, memory_id=ch.memory_id, kept_id=ch.kept_id,
        reason=reason,
        decided_by="owner", source=ch.source, before=here, after=now, undoes_id=ch.id,
    )  # fmt: skip
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:  # two clicks at once: the unique index lets one through
        raise ChangeError(409, "Already undone.") from None
    audit(session, "app", "consolidation_change_undone", change_id=ch.id, run_id=ch.run_id, memory_id=ch.memory_id)
    return row


def undo_run(session: Session, run: ConsolidationRun) -> int:
    """Undo everything one run changed, newest change first (so notes that absorbed several keep exact state)."""
    n = 0
    rows = session.scalars(
        select(ConsolidationChange)
        .where(ConsolidationChange.run_id == run.id, ConsolidationChange.kind.in_(KINDS))
        .order_by(ConsolidationChange.created_at.desc(), ConsolidationChange.id.desc())
    ).all()
    for ch in rows:
        try:
            undo(session, run.owner_person_id, ch.id)
            n += 1
        except ChangeError:
            continue  # already undone, or the owner changed the note since: leave it
    return n
