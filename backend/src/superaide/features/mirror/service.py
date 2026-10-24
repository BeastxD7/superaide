"""The Mirror's setting, the weekly note (made once per week, on first look), and the owner's feedback."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.features.mirror import patterns
from superaide.platform.audit import audit
from superaide.platform.db.models import MirrorNote, MirrorSetting

NOT_USEFUL_LIMIT = 2


def setting(session: Session, owner_id: uuid.UUID) -> MirrorSetting:
    row = session.scalar(select(MirrorSetting).where(MirrorSetting.owner_person_id == owner_id))
    if row is None:
        row = MirrorSetting(owner_person_id=owner_id)
        session.add(row)
        session.flush()
    return row


def set_enabled(session: Session, owner_id: uuid.UUID, enabled: bool) -> int:
    """On or off. Off deletes every note (what the owner muted is kept, so turning it on again respects it)."""
    row = setting(session, owner_id)
    row.enabled = enabled
    deleted = 0
    if not enabled:
        for n in session.scalars(select(MirrorNote).where(MirrorNote.owner_person_id == owner_id)):
            session.delete(n)
            deleted += 1
    audit(session, "mirror", "enabled" if enabled else "disabled", person_id=owner_id, deleted=deleted)
    return deleted


def _shown(row: MirrorSetting, f: patterns.Finding) -> bool:
    return f.pattern_key not in (row.muted_patterns or []) and (row.not_useful or {}).get(f.kind, 0) < NOT_USEFUL_LIMIT


def this_weeks_note(session: Session, owner_id: uuid.UUID, now: datetime | None = None) -> MirrorNote | None:
    """The note for the current week, made the first time it's asked for. None if nothing is worth saying."""
    now = now or datetime.now(UTC)
    start = patterns.week_start(now.date())
    note = session.scalar(
        select(MirrorNote).where(MirrorNote.owner_person_id == owner_id, MirrorNote.week_start == start)
    )
    if note is not None:
        return note
    row = setting(session, owner_id)
    found = [f for f in patterns.mine(session, owner_id, now) if _shown(row, f)][: patterns.MAX_FINDINGS]
    if not found:
        return None
    note = MirrorNote(owner_person_id=owner_id, week_start=start, findings=[f.as_dict() for f in found])
    session.add(note)
    session.flush()
    audit(session, "mirror", "note_made", person_id=owner_id, findings=len(found))
    return note


def view(session: Session, owner_id: uuid.UUID) -> dict[str, Any]:
    row = setting(session, owner_id)
    note = this_weeks_note(session, owner_id) if row.enabled else None
    past = (
        session.scalars(
            select(MirrorNote)
            .where(MirrorNote.owner_person_id == owner_id)
            .order_by(MirrorNote.week_start.desc())
            .limit(5)
        ).all()
        if row.enabled
        else []
    )
    return {
        "enabled": row.enabled,
        "note": _note(note) if note else None,
        "earlier": [_note(n) for n in past if note is None or n.id != note.id],
        "muted": len(row.muted_patterns or []),
    }


def _note(n: MirrorNote) -> dict[str, Any]:
    return {"id": str(n.id), "week_start": n.week_start.isoformat(), "findings": n.findings}


def give_feedback(session: Session, owner_id: uuid.UUID, note_id: uuid.UUID, index: int, action: str) -> bool:
    """ "not_useful" (counts against that kind of pattern) or "mute" (never mention this exact pattern again)."""
    note = session.get(MirrorNote, note_id)
    if note is None or note.owner_person_id != owner_id or not 0 <= index < len(note.findings):
        return False
    finding = dict(note.findings[index])
    row = setting(session, owner_id)
    if action == "mute":
        row.muted_patterns = sorted({*(row.muted_patterns or []), str(finding["pattern_key"])})
    else:
        votes = dict(row.not_useful or {})
        votes[finding["kind"]] = int(votes.get(finding["kind"], 0)) + 1
        row.not_useful = votes
    finding["feedback"] = action
    note.findings = [*note.findings[:index], finding, *note.findings[index + 1 :]]  # a new list: JSONB change tracking
    audit(session, "mirror", "feedback", person_id=owner_id, kind=finding["kind"], feedback=action)
    return True
