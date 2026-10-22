"""The kill switch, and small per-instance state.

Freezing stops model calls and replies (ADR 0009 T3):
- per agent instance: one owner's Ava (`set_frozen`), or
- the whole platform (`set_platform_frozen`): the operator's switch.
An instance is frozen if either is on.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.db.models import AgentState
from superaide.tenancy.context import InstanceScope, scope_or_default

KEY = "frozen"
PLATFORM_KEY = "frozen"


def _row(session: Session, scope: InstanceScope, key: str) -> AgentState | None:
    return session.get(AgentState, (scope.tenant_id, scope.agent_id, key))


def platform_frozen(session: Session) -> bool:
    from superaide.tenancy.models import PlatformState

    row = session.get(PlatformState, PLATFORM_KEY)
    return bool(row and (row.value or {}).get("on"))


def instance_frozen(session: Session, scope: InstanceScope | None = None) -> bool:
    row = _row(session, scope or scope_or_default(), KEY)
    return bool(row and row.value.get("on"))


def is_frozen(session: Session) -> bool:
    """Whether the instance in scope may not call a model or reply (it, or the whole platform, is frozen)."""
    return platform_frozen(session) or instance_frozen(session)


def set_frozen(session: Session, on: bool, *, actor: str) -> None:
    """Freeze or unfreeze the instance in scope."""
    set_state(session, KEY, {"on": on})
    audit(session, actor, "kill_switch", on=on, scope="instance")


def set_platform_frozen(session: Session, on: bool, *, actor: str) -> None:
    """Freeze or unfreeze every instance on the server."""
    from superaide.tenancy.models import PlatformState

    row = session.get(PlatformState, PLATFORM_KEY)
    if row is None:
        row = PlatformState(key=PLATFORM_KEY, value={})
        session.add(row)
    row.value = {"on": on}
    audit(session, actor, "kill_switch", on=on, scope="platform")


def get_state(session: Session, key: str) -> dict:
    """A small JSON value stored for the instance in scope (channel health and similar)."""
    row = _row(session, scope_or_default(), key)
    return dict(row.value) if row else {}


def set_state(session: Session, key: str, value: dict) -> None:
    scope = scope_or_default()
    row = _row(session, scope, key)
    if row is None:
        row = AgentState(tenant_id=scope.tenant_id, agent_id=scope.agent_id, key=key, value={})
        session.add(row)
    row.value = value
