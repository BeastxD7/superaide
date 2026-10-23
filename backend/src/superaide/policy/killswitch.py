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
    from sqlalchemy.dialects.postgresql import insert

    from superaide.tenancy.models import PlatformState

    # An upsert: two operators (or two clicks) switching it at once must not race on creating the row.
    session.flush()  # keep what the caller already changed before objects are expired below
    stmt = insert(PlatformState).values(key=PLATFORM_KEY, value={"on": on})
    session.execute(stmt.on_conflict_do_update(index_elements=[PlatformState.key], set_={"value": {"on": on}}))
    session.expire_all()
    audit(session, actor, "kill_switch", on=on, scope="platform")


def get_state(session: Session, key: str) -> dict:
    """A small JSON value stored for the instance in scope (channel health and similar)."""
    row = _row(session, scope_or_default(), key)
    return dict(row.value) if row else {}


def set_state(session: Session, key: str, value: dict) -> None:
    from sqlalchemy.dialects.postgresql import insert

    scope = scope_or_default()
    # An upsert: two requests creating the same instance's row at once (two halts) must not race.
    session.flush()
    stmt = insert(AgentState).values(tenant_id=scope.tenant_id, agent_id=scope.agent_id, key=key, value=value)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[AgentState.tenant_id, AgentState.agent_id, AgentState.key], set_={"value": value}
        )
    )
    session.expire_all()
