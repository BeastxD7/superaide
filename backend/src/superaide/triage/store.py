"""Reading and creating an owner's triage settings, and the agent's current stage."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.db.models import Person
from superaide.policy.killswitch import get_state
from superaide.triage.models import TriageSettings
from superaide.triage.types import Stage


def settings_for(session: Session, owner: Person) -> TriageSettings:
    """The owner's triage settings, created with the proposed defaults the first time."""
    row = session.scalar(select(TriageSettings).where(TriageSettings.person_id == owner.id))
    if row is None:
        row = TriageSettings(person_id=owner.id)
        session.add(row)
        session.flush()
    return row


def current_stage(session: Session) -> Stage:
    """The trust stage from agent_state["stage"] (TR6 sets it, with measured gates); Infant until then."""
    raw = str(get_state(session, "stage").get("stage", Stage.INFANT)).lower()
    try:
        return Stage(raw)
    except ValueError:
        return Stage.INFANT
