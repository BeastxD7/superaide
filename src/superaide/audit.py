"""Append-only audit trail."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from superaide.db.models import AuditLog


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
