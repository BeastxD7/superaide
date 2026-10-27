"""Binding an approval to the exact content the owner was shown (issue #111).

An approval is a yes to *this* message, not to whatever is later found under the same draft id. The content
is reduced to a small canonical form and hashed when the approval is requested; the executor rebuilds it
from what is about to be sent and compares. Case and runs of white space are ignored, because Gmail
round-trips mail through MIME; everything else (recipients, thread, subject, words) must match.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from superaide.approvals.models import Approval


def content_hash(*, to: Iterable[str], thread_id: str, subject: str, text: str, html: str = "") -> str:
    """The hash of a draft: recipients, thread, subject and the text part, and the HTML part when there is one (an
    email with a signature block has both, and the owner's yes covers both)."""
    canonical = {
        "to": sorted({a.strip().lower() for a in to if a.strip()}),
        "thread": thread_id.strip(),
        "subject": " ".join(subject.split()).lower(),
        "text": " ".join(text.split()),
    }
    if html:
        canonical["html"] = " ".join(html.split())
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# --- every approval kind (issue #111, second step) ------------------------------------------------------
#
# The Gmail hash above covers the draft as Gmail holds it. This one covers the approval row itself: every
# field an executor may read (recipients, event, patch, notify flag, reply text, the summary on the card).
# It is taken when the approval is opened and re-derived by `resolve` just before anything runs. Only
# `result`, which `resolve` itself writes after the run, is left out; any other key is bound by default,
# so a field an executor starts to read later is covered without anyone remembering to list it.

_NOT_BOUND = frozenset({"result", "outcome"})


def payload_hash(kind: str, payload: dict[str, Any]) -> str:
    bound = {k: v for k, v in (payload or {}).items() if k not in _NOT_BOUND}
    text = json.dumps(
        {"kind": kind, "payload": bound}, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False
    )
    return hashlib.sha256(text.encode()).hexdigest()


APPROVAL_TTL_CHOICES = (0, 1, 24, 72, 168)  # hours the owner may pick for how long a request waits (0 = never)


def seal(approval: Approval) -> None:
    """Record what the owner is about to be shown: the hash of the payload and, when a lifetime is set, the
    moment the request stops being answerable. Call it once the payload is final, before the first flush."""
    from superaide.platform.config import get_settings

    settings = get_settings()
    if settings.approval_binding != "off":
        approval.bound_hash = payload_hash(approval.kind, approval.payload)
    from superaide.platform.db.session import session_scope
    from superaide.policy.killswitch import get_state

    with session_scope() as cfg:  # the owner's choice (Settings, #433), else the operator's
        chosen = (get_state(cfg, "settings:retention").get("read") or {}).get("approval_ttl_hours")
    hours = chosen if chosen in APPROVAL_TTL_CHOICES else settings.approval_ttl_hours
    if hours > 0:
        approval.expires_at = datetime.now(UTC) + timedelta(hours=hours)


def expired(approval: Approval, *, now: datetime | None = None) -> bool:
    return approval.expires_at is not None and (now or datetime.now(UTC)) >= approval.expires_at


def waiting() -> Any:
    """SQL condition for "still waiting for the owner's yes": pending and not past its expiry."""
    from sqlalchemy import and_, func, or_

    from superaide.approvals.models import Approval

    return and_(Approval.status == "pending", or_(Approval.expires_at.is_(None), Approval.expires_at > func.now()))


def check(approval: Approval) -> str:
    """`ok`, `mismatch` (the payload is not what was sealed) or `unsealed` (nothing was recorded)."""
    if not approval.bound_hash:
        return "unsealed"
    return "ok" if approval.bound_hash == payload_hash(approval.kind, approval.payload) else "mismatch"


def report(session: Session) -> dict[str, Any]:
    """What a switch from `record` to `strict` would have done, from the audit trail alone (this instance).

    `ready` means at least `MIN_SAMPLE` decided approvals were sealed and none of them ever mismatched, so
    strict would have refused nothing the owner had legitimately approved. Counts only, no content.
    """
    from sqlalchemy import func, select

    from superaide.approvals.models import Approval as Row
    from superaide.platform.config import get_settings
    from superaide.platform.db.models import AuditLog

    kinds: dict[str, dict[str, int]] = {}

    def row(kind: str) -> dict[str, int]:
        return kinds.setdefault(kind, {"approved": 0, "sealed": 0, "mismatches": 0, "unsealed": 0, "expired": 0})

    for kind, status, sealed, decided_by, n in session.execute(
        select(Row.kind, Row.status, Row.bound_hash.is_not(None), Row.decided_by, func.count()).group_by(
            Row.kind, Row.status, Row.bound_hash.is_not(None), Row.decided_by
        )
    ):
        r = row(kind)
        if decided_by == EXPIRED_BY:
            r["expired"] += n
        elif status == "approved":
            r["approved"] += n
            r["sealed" if sealed else "unsealed"] += n
    for log in session.scalars(select(AuditLog).where(AuditLog.action == "approval_payload_mismatch")):
        row(str(log.detail.get("kind", "?")))["mismatches"] += 1
    sealed_total = sum(r["sealed"] for r in kinds.values())
    mismatches = sum(r["mismatches"] for r in kinds.values())
    return {
        "mode": get_settings().approval_binding,
        "kinds": kinds,
        "sealed": sealed_total,
        "mismatches": mismatches,
        "ready": sealed_total >= MIN_SAMPLE and mismatches == 0,
        "min_sample": MIN_SAMPLE,
    }


MIN_SAMPLE = 20
EXPIRED_BY = "system (expired)"
