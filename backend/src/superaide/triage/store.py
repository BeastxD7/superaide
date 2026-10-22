"""Reading and creating an owner's triage settings, and the agent's current stage."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.db.models import Person
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
    """The trust stage from agent_state["stage"] (TR6 sets it, with measured gates); Infant until then.
    Read for the instance in scope (a job runs in its owner's), not the server's default instance."""
    from superaide.db.models import AgentState
    from superaide.tenancy.context import scope_or_default

    scope = scope_or_default()
    row = session.get(AgentState, (scope.tenant_id, scope.agent_id, "stage"))
    raw = str((row.value if row else {}).get("stage", Stage.INFANT)).lower()
    try:
        return Stage(raw)
    except ValueError:
        return Stage.INFANT
