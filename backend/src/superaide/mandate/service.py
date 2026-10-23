"""Mandates in the gateway (issue #112): sign, list, revoke, and decide whether a call runs without asking.

The ceiling is fixed here, not in a token: a mandate never covers `send` or `spend`, and never covers a
session that has read untrusted input while holding private data (the Rule of Two). Either way the call falls
back to the owner's approval, as it does today.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.config import get_settings
from superaide.db.models import Mandate
from superaide.mandate import token as tk
from superaide.tenancy.context import scope_or_default

NEVER_UNSUPERVISED = {"send", "spend"}


@dataclass(frozen=True)
class Caps:
    """What this session holds. All three together is refused as unsupervised action (Meta's Rule of Two)."""

    untrusted_input: bool
    private_data: bool
    can_act: bool

    @property
    def violates_rule_of_two(self) -> bool:
        return self.untrusted_input and self.private_data and self.can_act


def root_key() -> bytes:
    from superaide.vault.crypto import master_key

    return hmac.new(master_key()[0], b"superaide/mandate/v1", hashlib.sha256).digest()


def create(session: Session, owner_id: uuid.UUID, *, tools: list[str], expires: datetime, **limits: Any) -> Mandate:
    scope = scope_or_default()
    tok = tk.issue(
        root_key(), agent_id=scope.agent_id, issuer=owner_id, tools=tools, expires=expires.isoformat(), **limits
    )
    row = Mandate(token=tk.dumps(tok), card=tk.card(tok), expires_at=expires)
    session.add(row)
    session.flush()
    audit(session, "mandate", "mandate_signed", mandate_id=row.id, card=row.card)
    return row


def revoke(session: Session, mandate_id: uuid.UUID) -> None:
    row = session.get(Mandate, mandate_id)
    if row is not None and row.status == "active":
        row.status = "revoked"
        audit(session, "mandate", "mandate_revoked", mandate_id=row.id)


def permits(
    session: Session, *, tool: str, args: dict[str, Any], effect: str, caps: Caps, now: datetime | None = None
) -> bool:
    """True when an active mandate covers this call and it may run without asking. Counts the use."""
    if not get_settings().mandates_enabled:
        return False
    if effect in NEVER_UNSUPERVISED or caps.violates_rule_of_two or caps.untrusted_input:
        return False
    now = now or datetime.now(UTC)
    scope = scope_or_default()
    key = root_key()
    for row in session.scalars(select(Mandate).where(Mandate.status == "active", Mandate.expires_at > now)):
        try:
            tok = tk.loads(row.token)
        except tk.MandateError:
            continue
        verdict = tk.authorize(key, tok, agent_id=str(scope.agent_id), tool=tool, args=args, now=now, uses=row.uses)
        if verdict.ok:
            row.uses += 1
            audit(session, "mandate", "mandate_used", mandate_id=row.id, tool=tool)
            return True
    return False
