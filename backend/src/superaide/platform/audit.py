"""Append-only audit trail."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from superaide.identity.auth.models import AccountAudit
from superaide.platform.db.models import AuditLog


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def audit(session: Session, actor: str, action: str, **detail: Any) -> None:
    """Record a decision. UUIDs are stringified so the detail stays valid JSON."""
    session.add(AuditLog(actor=actor, action=action, detail=_jsonable(detail)))


def account_audit(session: Session, user_id: uuid.UUID | None, action: str, **detail: Any) -> None:
    """Record an event about an account itself: sign-up, e-mail checks, password and address changes.

    Accounts belong to the platform, not to an agent instance (ADR 0009), so where the line goes
    depends on whether the account has hired:
    - It has its own Ava: the line is in that Ava's audit log, like the rest of the account's activity there.
      (An account with several hires uses the oldest one.)
    - It has not hired yet (or the event is about nobody, like an unknown address): the line goes to the
      platform's account log, never into some other owner's Ava.
    """
    from superaide.platform.db.session import rescope
    from superaide.platform.tenancy.context import use_instance
    from superaide.platform.tenancy.service import instances_of, scope_of

    if user_id is not None:
        detail = {"user_id": user_id, **detail}
        hired = instances_of(session, user_id)
        if hired:
            with use_instance(scope_of(hired[0][0])):
                rescope(session)  # the row's instance is checked when it is written, so write it in here
                audit(session, "auth", action, **detail)
                session.flush()
            rescope(session)
            return
    session.add(AccountAudit(user_id=user_id, actor="auth", action=action, detail=_jsonable(detail)))
