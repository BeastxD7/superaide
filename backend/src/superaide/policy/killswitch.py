"""Kill switch: one flag that stops model calls and outbound messages."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.config import get_settings
from superaide.db.models import AgentState

KEY = "frozen"


def is_frozen(session: Session) -> bool:
    settings = get_settings()
    row = session.scalar(
        select(AgentState).where(
            AgentState.tenant_id == settings.tenant_id,
            AgentState.agent_id == settings.agent_id,
            AgentState.key == KEY,
        )
    )
    return bool(row and row.value.get("on"))


def set_frozen(session: Session, on: bool, *, actor: str) -> None:
    settings = get_settings()
    row = session.get(AgentState, (settings.tenant_id, settings.agent_id, KEY))
    if row is None:
        row = AgentState(tenant_id=settings.tenant_id, agent_id=settings.agent_id, key=KEY, value={})
        session.add(row)
    row.value = {"on": on}
    audit(session, actor, "kill_switch", on=on)
