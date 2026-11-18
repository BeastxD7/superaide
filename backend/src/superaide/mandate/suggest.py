"""Mandate suggestions (#408): after N identical approved requests Ava offers "Always do this kind of thing?".

Only a measurement and an offer: the owner's tap signs a NARROW mandate (one tool, 30 days, at most MAX_USES uses),
which the owner can revoke and which still passes every check in `service.skips_card` (flag, Infant stage, shadow
age, untainted, nobody else told). The offer is built here from the owner's own approved rows, never from anything
the model says, and the accept route re-derives it, so a client cannot ask for a wider one. Kinds that tell other
people, spend, send or delete are not in SUGGESTABLE, so they are never offered.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.approvals.batching import NEVER_BATCH
from superaide.approvals.models import Approval
from superaide.mandate import service
from superaide.mandate.models import Mandate
from superaide.platform.audit import audit
from superaide.platform.config import get_settings
from superaide.platform.db.models import AuditLog

# approval kind -> (mandate tool, card words). Own calendar, nobody else told.
SUGGESTABLE: dict[str, tuple[str, str]] = {
    "calendar_create_event": ("create_event", "Add events to your own calendar (no guests)"),
    "calendar_move_event": ("move_event", "Move events in your own calendar (no guests)"),
}
WINDOW_DAYS = 60
VALID_DAYS = 30
MAX_USES = 25
SNOOZE_DAYS = 30


def _covered(session: Session, tool: str, now: datetime) -> bool:
    return any(
        tool in service.tk.effective(service.tk.loads(m.token)).get("tools", [tool])
        for m in session.scalars(select(Mandate).where(Mandate.status == "active", Mandate.expires_at > now))
    )


def _snoozed(session: Session, kind: str, now: datetime) -> bool:
    since = now - timedelta(days=SNOOZE_DAYS)
    return any(
        (r.detail or {}).get("kind") == kind
        for r in session.scalars(
            select(AuditLog).where(AuditLog.action == "mandate_suggestion_dismissed", AuditLog.created_at >= since)
        )
    )


def _count(session: Session, kind: str, now: datetime) -> int:
    since = now - timedelta(days=WINDOW_DAYS)
    rows = session.scalars(
        select(Approval).where(Approval.kind == kind, Approval.status == "approved", Approval.decided_at >= since)
    )
    return sum(1 for a in rows if (a.payload or {}).get("tainted") is False and not (a.payload or {}).get("notify"))


def suggestions(session: Session, *, now: datetime | None = None) -> list[dict[str, Any]]:
    s = get_settings()
    if not (s.mandates_enabled and s.mandate_suggest_after >= 2):
        return []
    now = now or datetime.now(UTC)
    out = []
    for kind, (tool, words) in SUGGESTABLE.items():
        if kind in NEVER_BATCH or tool in service.NEVER_UNSUPERVISED:
            continue
        n = _count(session, kind, now)
        if n < s.mandate_suggest_after or _covered(session, tool, now) or _snoozed(session, kind, now):
            continue
        out.append(
            {
                "kind": kind,
                "count": n,
                "title": "Always do this kind of thing?",
                "words": words,
                "limits": f"{VALID_DAYS} days, at most {MAX_USES} times, you can revoke it any time",
            }
        )
    return out


def accept(session: Session, owner_id: uuid.UUID, kind: str, *, now: datetime | None = None) -> Mandate:
    now = now or datetime.now(UTC)
    offer = next((o for o in suggestions(session, now=now) if o["kind"] == kind), None)
    if offer is None:
        raise service.tk.MandateError("There is no offer for that.")
    tool, words = SUGGESTABLE[kind]
    row = service.create(
        session, owner_id, tools=[tool], expires=now + timedelta(days=VALID_DAYS), label=words[:120], max_uses=MAX_USES
    )
    audit(session, "mandate", "mandate_suggestion_accepted", kind=kind, mandate_id=row.id, after=offer["count"])
    return row


def dismiss(session: Session, kind: str) -> None:
    audit(session, "mandate", "mandate_suggestion_dismissed", kind=kind)
